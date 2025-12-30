import math
from pathlib import Path

import pytest

from step_to_scene.parser import (
    ORIGIN_KEYWORDS,
    StepAssembly,
    StepParser,
    _multiply_transforms,
)


@pytest.fixture
def test_step_file() -> Path:
    return Path(__file__).parent.parent / "test_step.step"


@pytest.mark.skipif(
    not (Path(__file__).parent.parent / "test_step.step").exists(),
    reason="test_step.step not found",
)
def test_nested_assembly_parsing(test_step_file: Path):
    parser = StepParser(test_step_file)
    assemblies = parser.parse()

    assert len(assemblies) >= 1, "Should find at least one root assembly"

    for asm in assemblies:
        assert asm.name is not None
        assert asm.id is not None

        for child in asm.children:
            assert child.parent is not None
            assert child.parent == asm

    for _aid, asm in parser.assemblies.items():
        for child in asm.children:
            assert child.parent == asm, "Parent-child relationship should be consistent"


class TestStepAssembly:
    def test_creation_basic(self):
        assembly = StepAssembly("TestAssembly", "#1", description="Test description")
        assert assembly.name == "TestAssembly"
        assert assembly.id == "#1"
        assert assembly.description == "Test description"
        assert assembly.children == []
        assert assembly.parent is None
        assert assembly.position == (0.0, 0.0, 0.0)
        assert assembly.rotation == (0.0, 0.0, 0.0)

    def test_creation_with_product_name(self):
        assembly = StepAssembly("Instance", "#1", product_name="OriginalProduct")
        assert assembly.name == "Instance"
        assert assembly.product_name == "OriginalProduct"

    def test_creation_product_name_defaults_to_name(self):
        assembly = StepAssembly("TestName", "#1")
        assert assembly.product_name == "TestName"

    def test_step_entity_id_parsing(self):
        assembly = StepAssembly("Test", "#100")
        assert assembly.step_entity_id == 99

    def test_step_entity_id_without_hash(self):
        assembly = StepAssembly("Test", "100")
        assert assembly.step_entity_id == 0

    def test_add_child(self):
        parent = StepAssembly("Parent", "#1")
        child = StepAssembly("Child", "#2")

        parent.add_child(child)

        assert child in parent.children
        assert child.parent == parent

    def test_add_multiple_children(self):
        parent = StepAssembly("Parent", "#1")
        child1 = StepAssembly("Child1", "#2")
        child2 = StepAssembly("Child2", "#3")

        parent.add_child(child1)
        parent.add_child(child2)

        assert len(parent.children) == 2
        assert child1.parent == parent
        assert child2.parent == parent

    def test_get_path_root(self):
        assembly = StepAssembly("Root", "#1")
        assert assembly.get_path() == "Root"

    def test_get_path_nested(self):
        parent = StepAssembly("Parent", "#1")
        child = StepAssembly("Child", "#2")
        grandchild = StepAssembly("Grandchild", "#3")

        parent.add_child(child)
        child.add_child(grandchild)

        assert grandchild.get_path() == "Parent/Child/Grandchild"

    def test_get_absolute_transform_no_parent(self):
        assembly = StepAssembly("Test", "#1")
        assembly.position = (1.0, 2.0, 3.0)
        assembly.rotation = (0.1, 0.2, 0.3)

        pos, rot = assembly.get_absolute_transform()
        assert pos == (1.0, 2.0, 3.0)
        assert rot == (0.1, 0.2, 0.3)

    def test_get_absolute_transform_with_parent(self):
        parent = StepAssembly("Parent", "#1")
        parent.position = (10.0, 0.0, 0.0)
        parent.rotation = (0.0, 0.0, 0.0)

        child = StepAssembly("Child", "#2")
        child.position = (5.0, 0.0, 0.0)
        child.rotation = (0.0, 0.0, 0.0)

        parent.add_child(child)

        pos, rot = child.get_absolute_transform()
        assert abs(pos[0] - 15.0) < 1e-6
        assert abs(pos[1]) < 1e-6
        assert abs(pos[2]) < 1e-6

    def test_repr(self):
        assembly = StepAssembly("Test", "#1")
        assembly.add_child(StepAssembly("Child", "#2"))

        repr_str = repr(assembly)
        assert "Test" in repr_str
        assert "#1" in repr_str
        assert "children=1" in repr_str


class TestMultiplyTransforms:
    def test_identity_parent(self):
        parent_pos = (0.0, 0.0, 0.0)
        parent_rot = (0.0, 0.0, 0.0)
        child_pos = (1.0, 2.0, 3.0)
        child_rot = (0.0, 0.0, 0.0)

        result_pos, result_rot = _multiply_transforms(
            parent_pos, parent_rot, child_pos, child_rot
        )

        assert abs(result_pos[0] - 1.0) < 1e-6
        assert abs(result_pos[1] - 2.0) < 1e-6
        assert abs(result_pos[2] - 3.0) < 1e-6

    def test_translation_only(self):
        parent_pos = (10.0, 20.0, 30.0)
        parent_rot = (0.0, 0.0, 0.0)
        child_pos = (1.0, 2.0, 3.0)
        child_rot = (0.0, 0.0, 0.0)

        result_pos, result_rot = _multiply_transforms(
            parent_pos, parent_rot, child_pos, child_rot
        )

        assert abs(result_pos[0] - 11.0) < 1e-6
        assert abs(result_pos[1] - 22.0) < 1e-6
        assert abs(result_pos[2] - 33.0) < 1e-6

    def test_rotation_90_yaw(self):
        parent_pos = (0.0, 0.0, 0.0)
        parent_rot = (0.0, 0.0, math.pi / 2)
        child_pos = (1.0, 0.0, 0.0)
        child_rot = (0.0, 0.0, 0.0)

        result_pos, _ = _multiply_transforms(
            parent_pos, parent_rot, child_pos, child_rot
        )

        assert abs(result_pos[0]) < 1e-6
        assert abs(result_pos[1] - 1.0) < 1e-6

    def test_combined_rotation(self):
        parent_pos = (0.0, 0.0, 0.0)
        parent_rot = (0.0, 0.0, math.pi / 4)
        child_pos = (0.0, 0.0, 0.0)
        child_rot = (0.0, 0.0, math.pi / 4)

        _, result_rot = _multiply_transforms(
            parent_pos, parent_rot, child_pos, child_rot
        )

        assert abs(result_rot[2] - math.pi / 2) < 1e-6


class TestStepParser:
    def test_parser_initialization(self, tmp_path: Path):
        step_file = tmp_path / "test.step"
        step_file.write_text("")

        parser = StepParser(step_file)

        assert parser.filepath == step_file
        assert parser.assemblies == {}
        assert parser.root_assemblies == []
        assert parser.unit_scale == 1.0
        assert parser.unit_name == "UNKNOWN"

    def test_parse_empty_data_section(self, tmp_path: Path):
        step_content = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('Test'), '2;1');
ENDSEC;
DATA;
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        parser = StepParser(step_file)
        assemblies = parser.parse()

        assert len(assemblies) == 1
        assert assemblies[0].name == "Assembly"

    def test_parse_missing_data_section(self, tmp_path: Path):
        step_content = """ISO-10303-21;
HEADER;
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        parser = StepParser(step_file)
        with pytest.raises(ValueError) as exc_info:
            parser.parse()
        assert "Could not find DATA section" in str(exc_info.value)

    def test_parse_with_products(self, tmp_path: Path):
        step_content = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1=PRODUCT('prod1','PartA','Description',());
#2=PRODUCT('prod2','PartB','Another desc',());
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        parser = StepParser(step_file)
        assemblies = parser.parse()

        assert len(assemblies) == 2
        names = {a.name for a in assemblies}
        assert "PartA" in names
        assert "PartB" in names

    def test_parse_detects_millimeter_units(self, tmp_path: Path):
        step_content = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        parser = StepParser(step_file)
        parser.parse()

        unit_name, unit_scale = parser.get_unit_info()
        assert unit_name == "MILLIMETER"
        assert unit_scale == 0.001

    def test_parse_detects_centimeter_units(self, tmp_path: Path):
        step_content = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.CENTI.,.METRE.));
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        parser = StepParser(step_file)
        parser.parse()

        unit_name, unit_scale = parser.get_unit_info()
        assert unit_name == "CENTIMETER"
        assert unit_scale == 0.01

    def test_parse_detects_meter_units(self, tmp_path: Path):
        step_content = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1=(LENGTH_UNIT()SI_UNIT($,.METRE.));
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        parser = StepParser(step_file)
        parser.parse()

        unit_name, unit_scale = parser.get_unit_info()
        assert unit_name == "METER"
        assert unit_scale == 1.0

    def test_parse_detects_origin_keywords(self, tmp_path: Path):
        step_content = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1=PRODUCT('prod1','world_origin','Origin point',());
#2=PRODUCT('prod2','regular_part','Regular part',());
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        parser = StepParser(step_file)
        assemblies = parser.parse()

        origin_asm = next((a for a in assemblies if "origin" in a.name.lower()), None)
        regular_asm = next((a for a in assemblies if "regular" in a.name.lower()), None)

        assert origin_asm is not None
        assert origin_asm.is_origin
        assert regular_asm is not None
        assert not regular_asm.is_origin


class TestOriginDetection:
    def test_origin_keywords_exist(self):
        assert "origin" in ORIGIN_KEYWORDS
        assert "base" in ORIGIN_KEYWORDS
        assert "world" in ORIGIN_KEYWORDS
        assert "root" in ORIGIN_KEYWORDS
        assert "reference" in ORIGIN_KEYWORDS
        assert "frame" in ORIGIN_KEYWORDS

    def test_origin_detection_various_keywords(self):
        test_cases = [
            ("robot_origin", True),
            ("base_link", True),
            ("world_frame", True),
            ("root_assembly", True),
            ("reference_point", True),
            ("coordinate_frame", True),
            ("gripper_part", False),
            ("motor_assembly", False),
            ("sensor_mount", False),
        ]

        for name, expected in test_cases:
            assembly = StepAssembly(name, "#1")
            assembly.is_origin = any(kw in name.lower() for kw in ORIGIN_KEYWORDS)
            assert assembly.is_origin == expected, f"Failed for {name}"


def test_step_assembly_creation():
    assembly = StepAssembly("TestAssembly", "#1", description="Test description")
    assert assembly.name == "TestAssembly"
    assert assembly.id == "#1"
    assert assembly.description == "Test description"
    assert assembly.children == []
    assert assembly.parent is None
    assert assembly.position == (0.0, 0.0, 0.0)
    assert assembly.rotation == (0.0, 0.0, 0.0)


def test_step_assembly_add_child():
    parent = StepAssembly("Parent", "#1")
    child = StepAssembly("Child", "#2")

    parent.add_child(child)

    assert child in parent.children
    assert child.parent == parent


def test_step_assembly_get_path():
    parent = StepAssembly("Parent", "#1")
    child = StepAssembly("Child", "#2")
    parent.add_child(child)

    assert parent.get_path() == "Parent"
    assert child.get_path() == "Parent/Child"


def test_step_assembly_get_absolute_transform_no_parent():
    assembly = StepAssembly("Test", "#1")
    assembly.position = (1.0, 2.0, 3.0)
    assembly.rotation = (0.1, 0.2, 0.3)

    pos, rot = assembly.get_absolute_transform()
    assert pos == (1.0, 2.0, 3.0)
    assert rot == (0.1, 0.2, 0.3)


def test_multiply_transforms_identity():
    parent_pos = (0.0, 0.0, 0.0)
    parent_rot = (0.0, 0.0, 0.0)
    child_pos = (1.0, 2.0, 3.0)
    child_rot = (0.0, 0.0, 0.0)

    result_pos, result_rot = _multiply_transforms(
        parent_pos, parent_rot, child_pos, child_rot
    )

    assert abs(result_pos[0] - 1.0) < 1e-6
    assert abs(result_pos[1] - 2.0) < 1e-6
    assert abs(result_pos[2] - 3.0) < 1e-6


def test_step_assembly_origin_detection():
    origin_asm = StepAssembly("robot_origin", "#1")
    origin_asm.is_origin = any(
        kw in "robot_origin".lower()
        for kw in ["origin", "base", "world", "root", "reference", "frame"]
    )
    assert origin_asm.is_origin

    normal_asm = StepAssembly("gripper_part", "#2")
    normal_asm.is_origin = any(
        kw in "gripper_part".lower()
        for kw in ["origin", "base", "world", "root", "reference", "frame"]
    )
    assert not normal_asm.is_origin
