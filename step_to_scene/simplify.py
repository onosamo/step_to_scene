"""Mesh simplification utilities for collision meshes."""

from pathlib import Path
from xml.etree import ElementTree as ET

import trimesh


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
    
    # Parse URDF
    tree = ET.parse(urdf_path)
    root = tree.getroot()
    
    # Track meshes to simplify
    mesh_files = set()
    mesh_elements = []
    
    # Find all mesh references in collision and optionally visual elements
    for link in root.findall('.//link'):
        # Collision meshes
        for collision in link.findall('.//collision/geometry/mesh'):
            mesh_file = collision.get('filename')
            if mesh_file:
                mesh_elements.append(('collision', collision, mesh_file))
                mesh_files.add(mesh_file)
        
        # Visual meshes (if not collision_only)
        if not collision_only:
            for visual in link.findall('.//visual/geometry/mesh'):
                mesh_file = visual.get('filename')
                if mesh_file:
                    mesh_elements.append(('visual', visual, mesh_file))
                    mesh_files.add(mesh_file)
    
    if not mesh_files:
        if progress_callback:
            progress_callback("No mesh files found in URDF")
        return
    
    if progress_callback:
        progress_callback(f"Found {len(mesh_files)} unique mesh files to simplify")
    
    # Resolve mesh paths relative to URDF location
    urdf_dir = urdf_path.parent
    simplified_meshes = {}
    
    for idx, mesh_file in enumerate(mesh_files, 1):
        # Resolve the mesh path
        if mesh_file.startswith("package://"):
            if progress_callback:
                progress_callback(f"  ⚠ Skipping ROS package path: {mesh_file}")
            continue
        
        mesh_path = urdf_dir / mesh_file
        
        if not mesh_path.exists():
            if progress_callback:
                progress_callback(f"  ⚠ Mesh file not found: {mesh_path}")
            continue
        
        # Simplify the mesh
        try:
            if progress_callback:
                progress_callback(f"Processing mesh {idx}/{len(mesh_files)}: {mesh_path.name}")
            
            simplified_path = simplify_mesh(
                mesh_path,
                offset=offset,
                visualize=False,
                output_path=mesh_path.with_name(f"simplified_{mesh_path.name}"),
                progress_callback=progress_callback,
            )
            simplified_meshes[mesh_file] = simplified_path.relative_to(urdf_dir)
        except Exception as e:
            if progress_callback:
                progress_callback(f"  ⚠ Failed to simplify {mesh_path.name}: {e}")
    
    # Update URDF if requested
    if update_urdf and simplified_meshes:
        if progress_callback:
            progress_callback(f"\nUpdating URDF to reference simplified meshes...")
        
        for mesh_type, element, original_file in mesh_elements:
            if original_file in simplified_meshes:
                new_path = str(simplified_meshes[original_file])
                element.set('filename', new_path)
                if progress_callback:
                    progress_callback(f"  Updated {mesh_type}: {original_file} → {new_path}")
        
        # Save updated URDF
        output_urdf = urdf_path.with_name(f"{urdf_path.stem}_simplified{urdf_path.suffix}")
        tree.write(output_urdf, encoding='utf-8', xml_declaration=True)
        if progress_callback:
            progress_callback(f"\n✓ Updated URDF saved to: {output_urdf}")
    
    if progress_callback:
        progress_callback(f"\n✓ Simplified {len(simplified_meshes)} mesh files")
