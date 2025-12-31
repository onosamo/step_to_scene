from pathlib import Path

import numpy as np
import pytest
import trimesh

from step_to_scene.simplify import (
    offset_mesh_surface,
    parse_urdf_for_mesh,
    simplify_urdf_meshes,
)


class TestParseUrdfForMesh:
    def test_parse_urdf_with_mesh(self, tmp_path: Path):
        urdf_content = """<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link">
        <collision>
            <geometry>
                <mesh filename="meshes/part.stl" scale="1.0 1.0 1.0"/>
            </geometry>
        </collision>
    </link>
</robot>
"""
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)

        mesh_file, scale = parse_urdf_for_mesh(urdf_file)

        assert mesh_file == "meshes/part.stl"
        assert scale == [1.0, 1.0, 1.0]

    def test_parse_urdf_without_mesh(self, tmp_path: Path):
        urdf_content = """<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link">
        <collision>
            <geometry>
                <box size="1 1 1"/>
            </geometry>
        </collision>
    </link>
</robot>
"""
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)

        mesh_file, scale = parse_urdf_for_mesh(urdf_file)

        assert mesh_file is None
        assert scale is None

    def test_parse_urdf_mesh_no_scale(self, tmp_path: Path):
        urdf_content = """<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link">
        <collision>
            <geometry>
                <mesh filename="meshes/part.stl"/>
            </geometry>
        </collision>
    </link>
</robot>
"""
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)

        mesh_file, scale = parse_urdf_for_mesh(urdf_file)

        assert mesh_file == "meshes/part.stl"
        assert scale == [1.0, 1.0, 1.0]

    def test_parse_urdf_visual_mesh(self, tmp_path: Path):
        urdf_content = """<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link">
        <visual>
            <geometry>
                <mesh filename="meshes/visual.stl"/>
            </geometry>
        </visual>
    </link>
</robot>
"""
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)

        mesh_file, scale = parse_urdf_for_mesh(urdf_file)

        assert mesh_file == "meshes/visual.stl"


class TestOffsetMeshSurface:
    def test_offset_simple_cube(self):
        mesh = trimesh.creation.box(extents=[1, 1, 1])

        offset_distance = 0.1
        offset_mesh = offset_mesh_surface(mesh, offset_distance)

        assert isinstance(offset_mesh, trimesh.Trimesh)
        assert len(offset_mesh.vertices) == len(mesh.vertices)
        assert len(offset_mesh.faces) == len(mesh.faces)

    def test_offset_preserves_topology(self):
        mesh = trimesh.creation.icosphere(radius=1.0, subdivisions=1)

        offset_mesh = offset_mesh_surface(mesh, 0.05)

        assert len(offset_mesh.faces) == len(mesh.faces)
        assert len(offset_mesh.vertices) == len(mesh.vertices)

    def test_offset_zero_distance(self):
        mesh = trimesh.creation.box(extents=[1, 1, 1])
        original_vertices = mesh.vertices.copy()

        offset_mesh = offset_mesh_surface(mesh, 0.0)

        np.testing.assert_array_almost_equal(
            offset_mesh.vertices, original_vertices, decimal=5
        )

    def test_offset_negative_distance(self):
        mesh = trimesh.creation.icosphere(radius=1.0, subdivisions=1)
        original_bounds = mesh.bounds.copy()

        offset_mesh = offset_mesh_surface(mesh, -0.1)

        assert offset_mesh.bounds[1][0] < original_bounds[1][0]


class TestSimplifyUrdfMeshes:
    def test_simplify_urdf_no_meshes(self, tmp_path: Path):
        urdf_content = """<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link">
        <collision>
            <geometry>
                <box size="1 1 1"/>
            </geometry>
        </collision>
    </link>
</robot>
"""
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)

        messages: list[str] = []

        def callback(msg: str):
            messages.append(msg)

        simplify_urdf_meshes(urdf_file, progress_callback=callback)

        assert any("No mesh files found" in m for m in messages)

    def test_simplify_urdf_nonexistent_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            simplify_urdf_meshes(tmp_path / "nonexistent.urdf")

    def test_simplify_urdf_mesh_not_found(self, tmp_path: Path):
        urdf_content = """<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link">
        <collision>
            <geometry>
                <mesh filename="nonexistent/mesh.stl"/>
            </geometry>
        </collision>
    </link>
</robot>
"""
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)

        messages: list[str] = []

        def callback(msg: str):
            messages.append(msg)

        simplify_urdf_meshes(urdf_file, progress_callback=callback)

        assert any("not found" in m.lower() for m in messages)

    @pytest.mark.skip(
        reason="Requires coacd/trimesh decomposition which may not be available"
    )
    def test_simplify_urdf_with_mesh(self, tmp_path: Path):
        mesh_dir = tmp_path / "meshes"
        mesh_dir.mkdir()
        mesh_file = mesh_dir / "part.stl"
        box_mesh = trimesh.creation.box(extents=[1, 1, 1])
        box_mesh.export(mesh_file)

        urdf_content = """<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link">
        <collision>
            <geometry>
                <mesh filename="meshes/part.stl"/>
            </geometry>
        </collision>
    </link>
</robot>
"""
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)

        simplify_urdf_meshes(urdf_file, offset=6.0, update_urdf=True)

        simplified_mesh = mesh_dir / "simplified_part.stl"
        assert simplified_mesh.exists()

    def test_simplify_urdf_xacro_includes(self, tmp_path: Path):
        parts_dir = tmp_path / "parts"
        parts_dir.mkdir()

        part_urdf = parts_dir / "part.urdf"
        part_urdf.write_text("""<?xml version="1.0"?>
<robot name="part">
    <link name="part_link">
        <collision>
            <geometry>
                <box size="1 1 1"/>
            </geometry>
        </collision>
    </link>
</robot>
""")

        main_xacro = tmp_path / "main.xacro"
        main_xacro.write_text("""<?xml version="1.0"?>
<robot name="main" xmlns:xacro="http://www.ros.org/wiki/xacro">
    <xacro:include filename="parts/part.urdf"/>
    <link name="base_link"/>
</robot>
""")

        messages: list[str] = []

        def callback(msg: str):
            messages.append(msg)

        simplify_urdf_meshes(main_xacro, progress_callback=callback)

        assert any("included URDF files" in m for m in messages)
