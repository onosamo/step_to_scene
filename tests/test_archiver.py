import tarfile
from pathlib import Path

import pytest

from step_to_scene.archiver import (
    archive_assembly,
    collect_urdf_dependencies,
    create_archive,
)


class TestCollectUrdfDependencies:
    def test_collect_single_urdf(self, tmp_path: Path):
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

        deps = collect_urdf_dependencies(urdf_file)

        assert urdf_file in deps
        assert len(deps) == 1

    def test_collect_urdf_with_mesh(self, tmp_path: Path):
        mesh_dir = tmp_path / "meshes"
        mesh_dir.mkdir()
        mesh_file = mesh_dir / "part.stl"
        mesh_file.write_text("solid test\nendsolid test")

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

        deps = collect_urdf_dependencies(urdf_file)

        assert urdf_file in deps
        assert mesh_file in deps
        assert len(deps) == 2

    def test_collect_urdf_with_xacro_include(self, tmp_path: Path):
        parts_dir = tmp_path / "parts"
        parts_dir.mkdir()

        included_urdf = parts_dir / "part1.urdf"
        included_urdf.write_text("""<?xml version="1.0"?>
<robot name="part1">
    <link name="part1_link"/>
</robot>
""")

        main_xacro = tmp_path / "main.xacro"
        main_xacro.write_text("""<?xml version="1.0"?>
<robot name="main" xmlns:xacro="http://www.ros.org/wiki/xacro">
    <xacro:include filename="parts/part1.urdf"/>
    <link name="base_link"/>
</robot>
""")

        deps = collect_urdf_dependencies(main_xacro)

        assert main_xacro in deps
        assert included_urdf in deps

    def test_collect_urdf_nonexistent_mesh(self, tmp_path: Path):
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

        deps = collect_urdf_dependencies(urdf_file)

        assert urdf_file in deps
        assert len(deps) == 1

    def test_collect_urdf_package_prefix(self, tmp_path: Path):
        mesh_dir = tmp_path / "meshes"
        mesh_dir.mkdir()
        mesh_file = mesh_dir / "part.stl"
        mesh_file.write_text("solid test\nendsolid test")

        urdf_content = """<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link">
        <collision>
            <geometry>
                <mesh filename="package://meshes/part.stl"/>
            </geometry>
        </collision>
    </link>
</robot>
"""
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)

        deps = collect_urdf_dependencies(urdf_file)

        assert urdf_file in deps
        assert mesh_file in deps


class TestCreateArchive:
    def test_create_archive_basic(self, tmp_path: Path):
        urdf_content = """<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link"/>
</robot>
"""
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)
        archive_path = tmp_path / "test_archive.tar.gz"

        create_archive(urdf_file, archive_path, include_step=False)

        assert archive_path.exists()
        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
            assert "test.urdf" in names

    def test_create_archive_with_referenced_meshes(self, tmp_path: Path):
        """Archive should only include meshes that are referenced in URDF."""
        mesh_dir = tmp_path / "meshes"
        mesh_dir.mkdir()
        (mesh_dir / "referenced.stl").write_text("solid\nendsolid")
        (mesh_dir / "unreferenced.stl").write_text("solid\nendsolid")

        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text("""<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link">
        <collision>
            <geometry>
                <mesh filename="meshes/referenced.stl"/>
            </geometry>
        </collision>
    </link>
</robot>
""")

        archive_path = tmp_path / "test_archive.tar.gz"

        create_archive(urdf_file, archive_path, include_step=False)

        assert archive_path.exists()
        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
            assert "test.urdf" in names
            assert any("referenced.stl" in n for n in names)
            assert not any("unreferenced.stl" in n for n in names)

    def test_create_archive_with_referenced_parts(self, tmp_path: Path):
        """Archive should only include parts that are referenced via xacro:include."""
        parts_dir = tmp_path / "parts"
        parts_dir.mkdir()
        (parts_dir / "included.urdf").write_text("""<?xml version="1.0"?>
<robot name="included">
    <link name="included_link"/>
</robot>
""")
        (parts_dir / "not_included.urdf").write_text("""<?xml version="1.0"?>
<robot name="not_included">
    <link name="not_included_link"/>
</robot>
""")

        urdf_file = tmp_path / "test.xacro"
        urdf_file.write_text("""<?xml version="1.0"?>
<robot name="test_robot" xmlns:xacro="http://www.ros.org/wiki/xacro">
    <xacro:include filename="parts/included.urdf"/>
    <link name="base_link"/>
</robot>
""")

        archive_path = tmp_path / "test_archive.tar.gz"

        create_archive(urdf_file, archive_path, include_step=False)

        assert archive_path.exists()
        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
            assert "test.xacro" in names
            assert any("included.urdf" in n for n in names)
            assert not any("not_included.urdf" in n for n in names)

    def test_create_archive_with_step_file(self, tmp_path: Path):
        urdf_file = tmp_path / "test_converted.urdf"
        urdf_file.write_text("""<?xml version="1.0"?>
<robot name="test_robot">
    <link name="base_link"/>
</robot>
""")
        step_file = tmp_path / "test.step"
        step_file.write_text("ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;")

        archive_path = tmp_path / "test_archive.tar.gz"

        create_archive(urdf_file, archive_path, include_step=True)

        assert archive_path.exists()
        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
            assert any("test.step" in n for n in names)

    def test_create_archive_nonexistent_file(self, tmp_path: Path):
        archive_path = tmp_path / "test_archive.tar.gz"

        with pytest.raises(FileNotFoundError):
            create_archive(tmp_path / "nonexistent.urdf", archive_path)

    def test_create_archive_progress_callback(self, tmp_path: Path):
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text("<robot/>")
        archive_path = tmp_path / "test_archive.tar.gz"

        messages: list[str] = []

        def callback(msg: str):
            messages.append(msg)

        create_archive(
            urdf_file, archive_path, include_step=False, progress_callback=callback
        )

        assert len(messages) > 0
        assert any("Collecting dependencies" in m for m in messages)
        assert any("Archive created" in m for m in messages)


class TestArchiveAssembly:
    def test_archive_assembly_basic(self, tmp_path: Path):
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text("<robot/>")

        original, simplified = archive_assembly(
            urdf_file, output_dir=tmp_path, include_step=False, create_simplified=False
        )

        assert original.exists()
        assert simplified is None

    def test_archive_assembly_with_simplified(self, tmp_path: Path):
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text("<robot/>")

        simplified_file = tmp_path / "test_simplified.urdf"
        simplified_file.write_text("<robot name='simplified'/>")

        original, simplified = archive_assembly(
            urdf_file, output_dir=tmp_path, include_step=False, create_simplified=True
        )

        assert original.exists()
        assert simplified is not None
        assert simplified.exists()

    def test_archive_assembly_default_output_dir(self, tmp_path: Path):
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text("<robot/>")

        original, simplified = archive_assembly(
            urdf_file, output_dir=None, include_step=False, create_simplified=False
        )

        assert original.parent == tmp_path
        assert original.exists()

    def test_archive_assembly_creates_output_dir(self, tmp_path: Path):
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text("<robot/>")
        output_dir = tmp_path / "archives" / "subdir"

        original, _ = archive_assembly(
            urdf_file,
            output_dir=output_dir,
            include_step=False,
            create_simplified=False,
        )

        assert output_dir.exists()
        assert original.exists()

    def test_archive_assembly_no_simplified_file(self, tmp_path: Path):
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text("<robot/>")

        messages: list[str] = []

        def callback(msg: str):
            messages.append(msg)

        original, simplified = archive_assembly(
            urdf_file,
            output_dir=tmp_path,
            include_step=False,
            create_simplified=True,
            progress_callback=callback,
        )

        assert original.exists()
        assert simplified is None
        assert any("No simplified version found" in m for m in messages)
