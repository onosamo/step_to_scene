import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from step_to_scene.xml_utils import (
    find_xacro_includes,
    get_mesh_info,
    parse_xml_safe,
    parse_xml_with_comments,
)


@pytest.fixture
def temp_xml_file():
    content = """<?xml version="1.0"?>
<robot name="test">
  <link name="base_link">
    <collision>
      <geometry>
        <mesh filename="meshes/test.stl" scale="0.001 0.001 0.001"/>
      </geometry>
    </collision>
  </link>
</robot>
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".urdf", delete=False) as f:
        f.write(content)
        f.flush()
        yield Path(f.name)

    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def temp_xacro_file():
    content = """<?xml version="1.0"?>
<robot name="test" xmlns:xacro="http://www.ros.org/wiki/xacro">
  <xacro:include filename="part1.urdf"/>
  <xacro:include filename="part2.urdf"/>
  <link name="world"/>
</robot>
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".xacro", delete=False) as f:
        f.write(content)
        f.flush()
        yield Path(f.name)

    Path(f.name).unlink(missing_ok=True)


def test_parse_xml_with_comments(temp_xml_file):
    tree = parse_xml_with_comments(temp_xml_file)
    root = tree.getroot()

    assert root.tag == "robot"
    assert root.get("name") == "test"


def test_parse_xml_safe(temp_xml_file):
    root = parse_xml_safe(temp_xml_file)

    assert root.tag == "robot"
    assert root.get("name") == "test"


def test_parse_xml_safe_with_malformed():
    content = """<?xml version="1.0"?>
<!-- This is a comment -->
<robot name="test">
  <link name="base"/>
</robot>
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".urdf", delete=False) as f:
        f.write(content)
        f.flush()
        temp_path = Path(f.name)

    try:
        root = parse_xml_safe(temp_path)
        assert root.tag == "robot"
    finally:
        temp_path.unlink(missing_ok=True)


def test_find_xacro_includes(temp_xacro_file):
    tree = ET.parse(temp_xacro_file)
    root = tree.getroot()

    includes = find_xacro_includes(root)
    assert len(includes) == 2


def test_get_mesh_info(temp_xml_file):
    root = parse_xml_safe(temp_xml_file)
    mesh_filename, scale = get_mesh_info(root)

    assert mesh_filename == "meshes/test.stl"
    assert scale == [0.001, 0.001, 0.001]


def test_get_mesh_info_no_mesh():
    content = """<?xml version="1.0"?>
<robot name="test">
  <link name="base"/>
</robot>
"""
    root = ET.fromstring(content)
    mesh_filename, scale = get_mesh_info(root)

    assert mesh_filename is None
    assert scale == [1.0, 1.0, 1.0]


def test_get_mesh_info_default_scale():
    content = """<?xml version="1.0"?>
<robot name="test">
  <link name="base">
    <collision>
      <geometry>
        <mesh filename="test.stl"/>
      </geometry>
    </collision>
  </link>
</robot>
"""
    root = ET.fromstring(content)
    mesh_filename, scale = get_mesh_info(root)

    assert mesh_filename == "test.stl"
    assert scale == [1.0, 1.0, 1.0]
