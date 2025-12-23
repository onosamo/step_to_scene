"""Mesh simplification utilities for collision meshes."""

import re
from pathlib import Path
from xml.etree import ElementTree as ET

import trimesh


class CommentedTreeBuilder(ET.TreeBuilder):
    """TreeBuilder that preserves comments."""

    def comment(self, data):
        """Handle comments."""
        self.start(ET.Comment, {})
        self.data(data)
        self.end(ET.Comment)


def parse_xacro(
    xacro_path: Path,
) -> tuple[list[tuple[ET.Element, Path]], ET.ElementTree]:
    """Parse xacro file to extract included URDF files.

    Args:
        xacro_path: Path to the xacro file

    Returns:
        Tuple of (list of (include_element, urdf_path) tuples, tree)
    """
    parser = ET.XMLParser(target=CommentedTreeBuilder())
    tree = ET.parse(xacro_path, parser)
    root = tree.getroot()

    included_files = []
    ns = {"xacro": "http://www.ros.org/wiki/xacro"}

    for include in root.findall(".//xacro:include", ns):
        filename = include.get("filename")
        if filename:
            urdf_path = xacro_path.parent / filename
            if urdf_path.exists():
                included_files.append((include, urdf_path))

    for include in root.findall(".//include"):
        filename = include.get("filename")
        if filename:
            urdf_path = xacro_path.parent / filename
            if urdf_path.exists():
                included_files.append((include, urdf_path))

    return included_files, tree


def parse_urdf_for_mesh(urdf_path: Path) -> tuple[str | None, list[float] | None]:
    """Parse URDF file to extract mesh filename and scale.

    Args:
        urdf_path: Path to the URDF file

    Returns:
        Tuple of (mesh_filename, scale) or (None, None) if no mesh found
    """
    try:
        tree = ET.parse(urdf_path)
    except ET.ParseError:
        with open(urdf_path, encoding="utf-8") as f:
            content = f.read()
        content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
        root = ET.fromstring(content)
    else:
        root = tree.getroot()

    mesh = root.find(".//collision/geometry/mesh")
    if mesh is None:
        mesh = root.find(".//visual/geometry/mesh")

    if mesh is None:
        return None, None

    mesh_filename = mesh.get("filename")
    scale_str = mesh.get("scale", "1 1 1")
    scale = [float(x) for x in scale_str.split()]

    return mesh_filename, scale


def offset_mesh_surface(
    mesh: trimesh.Trimesh, offset_distance: float
) -> trimesh.Trimesh:
    """Apply surface offset to a mesh by moving vertices along normals.

    Args:
        mesh: Input mesh
        offset_distance: Distance to offset in mm

    Returns:
        Offset mesh
    """
    if mesh.vertex_normals is None or len(mesh.vertex_normals) == 0:
        mesh.rezero()
        mesh.vertex_normals = mesh.vertex_normals
    offset_vertices = mesh.vertices + mesh.vertex_normals * offset_distance
    offset_mesh = trimesh.Trimesh(
        vertices=offset_vertices, faces=mesh.faces, process=False
    )
    return offset_mesh


def simplify_mesh(
    mesh_path: Path,
    offset: float,
    visualize: bool = False,
    output_path: Path = None,
    progress_callback=None,
) -> Path:
    """Simplify a single mesh file using convex decomposition.

    Args:
        mesh_path: Path to the input mesh file
        offset: Offset distance for collision mesh in mm
        visualize: Whether to visualize the result
        output_path: Optional custom output path
        progress_callback: Optional callback function(message: str)

    Returns:
        Path to the simplified mesh file
    """
    if not mesh_path.exists():
        raise FileNotFoundError(f"File not found: {mesh_path}")

    if progress_callback:
        progress_callback(f"Simplifying {mesh_path.name} with offset: {offset}mm")

    original_mesh = trimesh.load_mesh(mesh_path)
    decomposed = trimesh.decomposition.convex_decomposition(original_mesh)
    convex_meshes = [trimesh.Trimesh(**part) for part in decomposed]
    simplified_mesh = trimesh.util.concatenate(convex_meshes)
    offset_mesh = offset_mesh_surface(simplified_mesh, offset)

    if output_path is None:
        output_path = mesh_path.with_name(f"simplified_{mesh_path.stem}.stl")

    offset_mesh.export(output_path)

    if progress_callback:
        progress_callback(f"  ✓ Saved to: {output_path}")

    if visualize:
        original_mesh.visual.face_colors = [255, 0, 0, 50]
        offset_mesh.visual.face_colors = [0, 0, 255, 80]
        trimesh.Scene([original_mesh, offset_mesh]).show()

    return output_path


def simplify_urdf_meshes(
    urdf_path: Path,
    offset: float = 6.0,
    update_urdf: bool = True,
    collision_only: bool = True,
    progress_callback=None,
) -> None:
    """Simplify all meshes referenced in a URDF file.

    Args:
        urdf_path: Path to the URDF/XACRO file
        offset: Offset distance for collision meshes in mm
        update_urdf: Whether to update the URDF to reference simplified meshes
        collision_only: Whether to only simplify collision meshes (not visual)
        progress_callback: Optional callback function(message: str)
    """
    if not urdf_path.exists():
        raise FileNotFoundError(f"URDF file not found: {urdf_path}")

    if progress_callback:
        progress_callback(f"Processing URDF: {urdf_path}")

    # Check if this is a xacro file and parse included URDFs
    included_urdfs = []
    include_elements = []
    root_tree = None
    if urdf_path.suffix in [".xacro", ".urdf.xacro"]:
        parsed_elements, root_tree = parse_xacro(urdf_path)
        for element, urdf in parsed_elements:
            include_elements.append(element)
            included_urdfs.append(urdf)
        if progress_callback:
            progress_callback(
                f"Found {len(included_urdfs)} included URDF files from xacro"
            )

    # If no included files or not a xacro, process the file itself
    urdfs_to_process = included_urdfs if included_urdfs else [urdf_path]

    # Track meshes to simplify across all URDF files
    mesh_files = set()
    all_mesh_elements = []

    for current_urdf in urdfs_to_process:
        if progress_callback:
            progress_callback(f"Parsing URDF file: {current_urdf.name}")

        # Use the helper function to parse the URDF for mesh info
        mesh_filename, scale = parse_urdf_for_mesh(current_urdf)

        if mesh_filename:
            # Resolve the mesh path relative to the URDF
            mesh_path = current_urdf.parent / mesh_filename
            if mesh_path.exists():
                mesh_files.add((str(mesh_path), current_urdf))
                if progress_callback:
                    progress_callback(f"  Found mesh: {mesh_filename}")
            else:
                if progress_callback:
                    progress_callback(f"  ⚠ Mesh file not found: {mesh_path}")

        # Also parse using ElementTree for full mesh element info (for updating)
        try:
            parser = ET.XMLParser(target=CommentedTreeBuilder())
            tree = ET.parse(current_urdf, parser)
        except ET.ParseError:
            with open(current_urdf, encoding="utf-8") as f:
                content = f.read()
            content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
            root = ET.fromstring(content)
            tree = ET.ElementTree(root)
        else:
            root = tree.getroot()

        # Find all mesh references in collision and optionally visual elements
        for link in root.findall(".//link"):
            # Collision meshes
            for collision in link.findall(".//collision/geometry/mesh"):
                mesh_file = collision.get("filename")
                if mesh_file:
                    all_mesh_elements.append(
                        (current_urdf, tree, "collision", collision, mesh_file)
                    )

            # Visual meshes (if not collision_only)
            if not collision_only:
                for visual in link.findall(".//visual/geometry/mesh"):
                    mesh_file = visual.get("filename")
                    if mesh_file:
                        all_mesh_elements.append(
                            (current_urdf, tree, "visual", visual, mesh_file)
                        )

    if not mesh_files:
        if progress_callback:
            progress_callback("No mesh files found in URDF")
        return

    if progress_callback:
        progress_callback(f"Found {len(mesh_files)} unique mesh files to simplify")

    # Simplify each mesh
    simplified_meshes = {}

    for idx, (mesh_path_str, _source_urdf) in enumerate(mesh_files, 1):
        mesh_path = Path(mesh_path_str)

        # Simplify the mesh
        try:
            if progress_callback:
                progress_callback(
                    f"Processing mesh {idx}/{len(mesh_files)}: {mesh_path.name}"
                )

            # Output simplified mesh in the same directory as original
            output_path = mesh_path.parent / f"simplified_{mesh_path.name}"

            simplified_path = simplify_mesh(
                mesh_path,
                offset=offset,
                visualize=False,
                output_path=output_path,
                progress_callback=progress_callback,
            )

            # Store the simplified path for this mesh
            simplified_meshes[str(mesh_path)] = simplified_path

        except Exception as e:
            if progress_callback:
                progress_callback(f"  ⚠ Failed to simplify {mesh_path.name}: {e}")
                import traceback

                progress_callback(traceback.format_exc())

    # Update URDF files if requested
    if update_urdf and simplified_meshes:
        if progress_callback:
            progress_callback("\nUpdating URDF files to reference simplified meshes...")

        # Group mesh elements by source URDF
        urdfs_to_update = {}
        for source_urdf, tree, mesh_type, element, original_file in all_mesh_elements:
            if source_urdf not in urdfs_to_update:
                urdfs_to_update[source_urdf] = (tree, [])
            urdfs_to_update[source_urdf][1].append((mesh_type, element, original_file))

        # Track simplified URDFs for updating xacro includes
        simplified_urdf_map = {}

        # Update and save each URDF
        for source_urdf, (tree, elements) in urdfs_to_update.items():
            updated = False
            for mesh_type, element, original_file in elements:
                # Resolve the original mesh path
                original_mesh_path = source_urdf.parent / original_file
                original_mesh_str = str(original_mesh_path)

                if original_mesh_str in simplified_meshes:
                    simplified_path = simplified_meshes[original_mesh_str]
                    # Get relative path from the URDF directory
                    try:
                        rel_path = simplified_path.relative_to(source_urdf.parent)
                    except ValueError:
                        # If can't get relative path, use the filename
                        rel_path = simplified_path.name

                    new_path = str(rel_path).replace("\\", "/")
                    element.set("filename", new_path)
                    if progress_callback:
                        progress_callback(
                            f"  Updated {mesh_type} in {source_urdf.name}: "
                            f"{original_file} → {new_path}"
                        )
                    updated = True

            # Save updated URDF if changes were made
            if updated:
                output_urdf = source_urdf.with_name(
                    f"{source_urdf.stem}_simplified{source_urdf.suffix}"
                )
                ET.register_namespace("xacro", "http://www.ros.org/wiki/xacro")
                tree.write(
                    output_urdf,
                    encoding="utf-8",
                    xml_declaration=True,
                    method="xml",
                )
                simplified_urdf_map[source_urdf] = output_urdf
                if progress_callback:
                    progress_callback(f"  ✓ Updated URDF saved to: {output_urdf}")

        # Update main xacro file if it has includes
        if root_tree is not None and include_elements:
            if progress_callback:
                progress_callback(
                    "\nUpdating main xacro file to reference simplified URDFs..."
                )

            # Update include elements to point to simplified URDFs
            for include_element, source_urdf in zip(
                include_elements, included_urdfs, strict=True
            ):
                if source_urdf in simplified_urdf_map:
                    simplified_urdf = simplified_urdf_map[source_urdf]
                    # Get relative path from main xacro directory
                    try:
                        rel_path = simplified_urdf.relative_to(urdf_path.parent)
                    except ValueError:
                        rel_path = simplified_urdf.name

                    new_filename = str(rel_path).replace("\\", "/")
                    include_element.set("filename", new_filename)
                    if progress_callback:
                        progress_callback(
                            f"  Updated include: {source_urdf.name} → {new_filename}"
                        )

            # Save the simplified main xacro file
            updated_xacro = urdf_path.with_name(
                f"{urdf_path.stem}_simplified{urdf_path.suffix}"
            )
            ET.register_namespace("xacro", "http://www.ros.org/wiki/xacro")
            root_tree.write(
                updated_xacro, encoding="utf-8", xml_declaration=True, method="xml"
            )
            if progress_callback:
                progress_callback(
                    f"  ✓ Simplified main xacro saved to: {updated_xacro}"
                )

    if progress_callback:
        progress_callback(f"\n✓ Simplified {len(simplified_meshes)} mesh files")
