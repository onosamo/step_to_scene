from pathlib import Path

import numpy as np

from step_to_scene.xml_utils import parse_urdf_mesh_info, parse_xacro_with_transforms


def rpy_to_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    Rx = np.array(
        [[1, 0, 0], [0, np.cos(roll), -np.sin(roll)], [0, np.sin(roll), np.cos(roll)]]
    )

    Ry = np.array(
        [
            [np.cos(pitch), 0, np.sin(pitch)],
            [0, 1, 0],
            [-np.sin(pitch), 0, np.cos(pitch)],
        ]
    )

    Rz = np.array(
        [[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]]
    )

    return Rz @ Ry @ Rx


def create_transform_matrix(xyz: list[float], rpy: list[float]) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = rpy_to_matrix(rpy[0], rpy[1], rpy[2])
    T[:3, 3] = xyz
    return T


def visualize_urdf(xacro_path: Path):
    import trimesh

    print(f"Processing XACRO file: {xacro_path}")

    included_urdfs, joint_transforms = parse_xacro_with_transforms(xacro_path)
    print(f"Found {len(included_urdfs)} included URDF files")
    print(f"Found {len(joint_transforms)} joint transformations")

    meshes = []
    mesh_names = []

    for urdf_path in included_urdfs:
        print(f"\nProcessing: {urdf_path.name}")

        mesh_filename, link_name, scale = parse_urdf_mesh_info(urdf_path)

        if mesh_filename is None:
            print(f"  No mesh found in {urdf_path.name}")
            continue

        mesh_path = urdf_path.parent / mesh_filename
        if not mesh_path.exists():
            print(f"  Mesh file not found: {mesh_path}")
            continue

        print(f"  Link: {link_name}")
        print(f"  Mesh: {mesh_path.name}")
        print(f"  Scale: {scale}")

        try:
            mesh = trimesh.load(mesh_path)
            if hasattr(mesh, "vertices"):
                print(f"  Loaded mesh with {len(mesh.vertices)} vertices")
        except Exception as e:
            print(f"  Failed to load mesh: {e}")
            continue

        if scale and scale != [1, 1, 1]:
            scale_matrix = np.eye(4)
            scale_matrix[0, 0] = scale[0]
            scale_matrix[1, 1] = scale[1]
            scale_matrix[2, 2] = scale[2]
            mesh.apply_transform(scale_matrix)
            print("  Applied scale")

        if link_name in joint_transforms:
            transform = joint_transforms[link_name]
            xyz = transform["xyz"]
            rpy = transform["rpy"]

            print(f"  Transform: xyz={xyz}, rpy={rpy}")

            T = create_transform_matrix(xyz, rpy)

            mesh.apply_transform(T)
            print("  Applied transformation")
        else:
            print(f"  No transformation found for {link_name}")

        meshes.append(mesh)
        mesh_names.append(link_name)

    print(f"\nProcessed {len(meshes)} meshes")

    if meshes:
        print("\nOpening visualization...")
        try:
            scene = trimesh.Scene()
            for mesh, name in zip(meshes, mesh_names, strict=False):
                color = np.random.randint(50, 255, size=3)
                if hasattr(mesh, "visual") and hasattr(mesh.visual, "vertex_colors"):
                    mesh.visual.vertex_colors = [*color, 255]
                scene.add_geometry(mesh, node_name=name)

            scene.show()
        except Exception as e:
            print(f"  Visualization failed: {e}")
