from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

import trimesh

from step_to_scene.xml_utils import (
    get_mesh_info,
    parse_xacro_includes,
    parse_xml_safe,
    parse_xml_with_comments,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def parse_urdf_for_mesh(urdf_path: Path) -> tuple[str | None, list[float] | None]:
    root = parse_xml_safe(urdf_path)
    mesh_filename, scale = get_mesh_info(root)
    if mesh_filename is None:
        return None, None
    return mesh_filename, scale


def offset_mesh_surface(
    mesh: trimesh.Trimesh, offset_distance: float
) -> trimesh.Trimesh:
    if mesh.vertex_normals is None or len(mesh.vertex_normals) == 0:
        mesh.rezero()
    offset_vertices = mesh.vertices + mesh.vertex_normals * offset_distance
    return trimesh.Trimesh(vertices=offset_vertices, faces=mesh.faces, process=False)


def simplify_mesh(
    mesh_path: Path,
    offset: float,
    visualize: bool = False,
    output_path: Path | None = None,
    progress_callback: "Callable[[str], None] | None" = None,
) -> Path:
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
        progress_callback(f"  Saved to: {output_path}")

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
    progress_callback: "Callable[[str], None] | None" = None,
) -> None:
    if not urdf_path.exists():
        raise FileNotFoundError(f"URDF file not found: {urdf_path}")

    if progress_callback:
        progress_callback(f"Processing URDF: {urdf_path}")

    included_urdfs: list[Path] = []
    include_elements: list[ET.Element] = []
    root_tree: ET.ElementTree | None = None

    if urdf_path.suffix in [".xacro", ".urdf.xacro"]:
        parsed_elements, root_tree = parse_xacro_includes(urdf_path)
        for element, urdf in parsed_elements:
            include_elements.append(element)
            included_urdfs.append(urdf)
        if progress_callback:
            progress_callback(
                f"Found {len(included_urdfs)} included URDF files from xacro"
            )

    urdfs_to_process = included_urdfs if included_urdfs else [urdf_path]

    mesh_files: set[tuple[str, Path]] = set()
    all_mesh_elements: list[tuple[Path, ET.ElementTree, str, ET.Element, str]] = []

    for current_urdf in urdfs_to_process:
        if progress_callback:
            progress_callback(f"Parsing URDF file: {current_urdf.name}")

        mesh_filename, scale = parse_urdf_for_mesh(current_urdf)

        if mesh_filename:
            mesh_path = current_urdf.parent / mesh_filename
            if mesh_path.exists():
                mesh_files.add((str(mesh_path), current_urdf))
                if progress_callback:
                    progress_callback(f"  Found mesh: {mesh_filename}")
            elif progress_callback:
                progress_callback(f"  Mesh file not found: {mesh_path}")

        tree = parse_xml_with_comments(current_urdf)
        root = tree.getroot()

        for link in root.findall(".//link"):
            for collision in link.findall(".//collision/geometry/mesh"):
                mesh_file = collision.get("filename")
                if mesh_file:
                    all_mesh_elements.append(
                        (current_urdf, tree, "collision", collision, mesh_file)
                    )

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

    simplified_meshes: dict[str, Path] = {}

    for idx, (mesh_path_str, _source_urdf) in enumerate(mesh_files, 1):
        mesh_path = Path(mesh_path_str)

        try:
            if progress_callback:
                progress_callback(
                    f"Processing mesh {idx}/{len(mesh_files)}: {mesh_path.name}"
                )

            output_path = mesh_path.parent / f"simplified_{mesh_path.name}"

            simplified_path = simplify_mesh(
                mesh_path,
                offset=offset,
                visualize=False,
                output_path=output_path,
                progress_callback=progress_callback,
            )

            simplified_meshes[str(mesh_path)] = simplified_path

        except Exception as e:
            if progress_callback:
                progress_callback(f"  Failed to simplify {mesh_path.name}: {e}")
                import traceback

                progress_callback(traceback.format_exc())

    if update_urdf and simplified_meshes:
        if progress_callback:
            progress_callback("\nUpdating URDF files to reference simplified meshes...")

        urdfs_to_update: dict[Path, tuple[ET.ElementTree, list]] = {}
        for source_urdf, tree, mesh_type, element, original_file in all_mesh_elements:
            if source_urdf not in urdfs_to_update:
                urdfs_to_update[source_urdf] = (tree, [])
            urdfs_to_update[source_urdf][1].append((mesh_type, element, original_file))

        simplified_urdf_map: dict[Path, Path] = {}

        for source_urdf, (tree, elements) in urdfs_to_update.items():
            updated = False
            for mesh_type, element, original_file in elements:
                original_mesh_path = source_urdf.parent / original_file
                original_mesh_str = str(original_mesh_path)

                if original_mesh_str in simplified_meshes:
                    simplified_path = simplified_meshes[original_mesh_str]
                    try:
                        rel_path = simplified_path.relative_to(source_urdf.parent)
                    except ValueError:
                        rel_path = Path(simplified_path.name)

                    new_path = str(rel_path).replace("\\", "/")
                    element.set("filename", new_path)
                    if progress_callback:
                        progress_callback(
                            f"  Updated {mesh_type} in {source_urdf.name}: "
                            f"{original_file} -> {new_path}"
                        )
                    updated = True

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
                    progress_callback(f"  Updated URDF saved to: {output_urdf}")

        if root_tree is not None and include_elements:
            if progress_callback:
                progress_callback(
                    "\nUpdating main xacro file to reference simplified URDFs..."
                )

            for include_element, source_urdf in zip(
                include_elements, included_urdfs, strict=True
            ):
                if source_urdf in simplified_urdf_map:
                    simplified_urdf = simplified_urdf_map[source_urdf]
                    try:
                        rel_path = simplified_urdf.relative_to(urdf_path.parent)
                    except ValueError:
                        rel_path = Path(simplified_urdf.name)

                    new_filename = str(rel_path).replace("\\", "/")
                    include_element.set("filename", new_filename)
                    if progress_callback:
                        progress_callback(
                            f"  Updated include: {source_urdf.name} -> {new_filename}"
                        )

            updated_xacro = urdf_path.with_name(
                f"{urdf_path.stem}_simplified{urdf_path.suffix}"
            )
            ET.register_namespace("xacro", "http://www.ros.org/wiki/xacro")
            root_tree.write(
                updated_xacro, encoding="utf-8", xml_declaration=True, method="xml"
            )
            if progress_callback:
                progress_callback(f"  Simplified main xacro saved to: {updated_xacro}")

    if progress_callback:
        progress_callback(f"\nSimplified {len(simplified_meshes)} mesh files")
