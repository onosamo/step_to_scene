from pathlib import Path

import pytest

from step_to_scene.exporters import (
    ExportEntry,
    ExportReport,
    URDFExporter,
    get_exporter,
    get_potential_base_links,
)
from step_to_scene.parser import StepAssembly


class TestGetExporter:
    def test_get_urdf_exporter(self):
        exporter = get_exporter("urdf")
        assert isinstance(exporter, URDFExporter)

    def test_get_urdf_exporter_case_insensitive(self):
        exporter = get_exporter("URDF")
        assert isinstance(exporter, URDFExporter)

    def test_get_invalid_format(self):
        with pytest.raises(ValueError) as exc_info:
            get_exporter("invalid_format")
        assert "Unsupported format" in str(exc_info.value)


class TestGetPotentialBaseLinks:
    def test_find_origin_assembly(self):
        origin_asm = StepAssembly("robot_origin", "#1")
        origin_asm.is_origin = True

        normal_asm = StepAssembly("gripper", "#2")
        normal_asm.is_origin = False

        result = get_potential_base_links([origin_asm, normal_asm])

        assert len(result) == 1
        assert result[0].name == "robot_origin"

    def test_find_origin_in_children(self):
        parent = StepAssembly("parent", "#1")
        parent.is_origin = False

        child_origin = StepAssembly("base_frame", "#2")
        child_origin.is_origin = True
        parent.add_child(child_origin)

        result = get_potential_base_links([parent])

        assert len(result) == 1
        assert result[0].name == "base_frame"

    def test_no_origins_found(self):
        asm1 = StepAssembly("part1", "#1")
        asm1.is_origin = False
        asm2 = StepAssembly("part2", "#2")
        asm2.is_origin = False

        result = get_potential_base_links([asm1, asm2])

        assert len(result) == 0

    def test_multiple_origins(self):
        asm1 = StepAssembly("world_origin", "#1")
        asm1.is_origin = True
        asm2 = StepAssembly("base_frame", "#2")
        asm2.is_origin = True

        result = get_potential_base_links([asm1, asm2])

        assert len(result) == 2


class TestSanitizeName:
    def test_sanitize_name_basic(self):
        exporter = URDFExporter()
        assert exporter._sanitize_name("part_name") == "part_name"

    def test_sanitize_name_special_chars(self):
        exporter = URDFExporter()
        assert exporter._sanitize_name("part-name.v1") == "part_name_v1"

    def test_sanitize_name_starts_with_digit(self):
        exporter = URDFExporter()
        assert exporter._sanitize_name("123_part") == "part_123_part"

    def test_sanitize_name_empty(self):
        exporter = URDFExporter()
        assert exporter._sanitize_name("") == "unnamed_part"

    def test_sanitize_name_all_special(self):
        exporter = URDFExporter()
        result = exporter._sanitize_name("---...###")
        # "---...###" is 9 characters, each replaced with "_"
        assert result == "_________"


class TestLinkNameAllocation:
    def test_unique_names_stay_unchanged(self):
        exporter = URDFExporter()
        assert exporter._allocate_link_name("part_a") == "part_a"
        assert exporter._allocate_link_name("part_b") == "part_b"

    def test_duplicate_names_get_suffixes(self):
        exporter = URDFExporter()
        assert exporter._allocate_link_name("IEP-013122") == "IEP_013122"
        assert exporter._allocate_link_name("IEP-013122") == "IEP_013122_2"
        assert exporter._allocate_link_name("IEP-013122") == "IEP_013122_3"

    def test_names_colliding_after_sanitize_get_suffixes(self):
        exporter = URDFExporter()
        assert exporter._allocate_link_name("part.a") == "part_a"
        assert exporter._allocate_link_name("part-a") == "part_a_2"


class TestExcludedPaths:
    def test_collects_relative_paths_of_excluded_descendants(self):
        root = StepAssembly("root", "#1")
        child_a = StepAssembly("a", "#1/#2")
        child_b = StepAssembly("b", "#1/#3")
        grandchild = StepAssembly("c", "#1/#3/#4")
        root.add_child(child_a)
        root.add_child(child_b)
        child_b.add_child(grandchild)
        child_a.occurrence_index = 0
        child_b.occurrence_index = 0
        grandchild.occurrence_index = 0

        exporter = URDFExporter()
        exporter.excluded_assemblies = {"#1/#2", "#1/#3/#4"}

        excluded = exporter._excluded_paths_under(root)

        assert excluded == {(("a", 0),), (("b", 0), ("c", 0))}

    def test_excluded_parent_hides_descendants(self):
        root = StepAssembly("root", "#1")
        child = StepAssembly("a", "#1/#2")
        grandchild = StepAssembly("b", "#1/#2/#3")
        root.add_child(child)
        child.add_child(grandchild)

        exporter = URDFExporter()
        exporter.excluded_assemblies = {"#1/#2", "#1/#2/#3"}

        excluded = exporter._excluded_paths_under(root)

        assert excluded == {(("a", 0),)}

    def test_no_exclusions(self):
        root = StepAssembly("root", "#1")
        root.add_child(StepAssembly("a", "#1/#2"))

        exporter = URDFExporter()

        assert exporter._excluded_paths_under(root) == set()


class TestXacroDescriptionComments:
    def test_main_xacro_contains_description_comments(self, tmp_path: Path):
        from step_to_scene.parser import StepParser

        step_content = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1=PRODUCT('p1','part_a','Lid cart - 60 degree loading',());
#2=PRODUCT('p2','part_b','',());
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        parser = StepParser(step_file)
        roots = parser.parse()

        exporter = URDFExporter()
        exporter.export(roots, tmp_path / "scene.xacro")

        text = (tmp_path / "scene.xacro").read_text()
        assert "<!-- Include part_a assembly (Lid cart - 60 degree loading) -->" in text
        assert "<!-- Include part_b assembly -->" in text

    def test_double_dash_in_description_stays_well_formed(self, tmp_path: Path):
        from step_to_scene.parser import StepParser

        step_content = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1=PRODUCT('p1','part_a','bracket -- rev A',());
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        parser = StepParser(step_file)
        roots = parser.parse()

        exporter = URDFExporter()
        exporter.export(roots, tmp_path / "scene.xacro")

        # must re-parse cleanly: '--' inside an XML comment is illegal
        from xml.etree import ElementTree as ET

        ET.parse(tmp_path / "scene.xacro")

    def test_report_includes_description(self, tmp_path: Path):
        report = ExportReport(
            entries=[
                ExportEntry(
                    "IEP-034005",
                    "IEP_034005",
                    "IEP_034005.stl",
                    description="Lid cart - 60 degree loading",
                )
            ]
        )
        report_path = tmp_path / "report.txt"
        report.write(report_path)
        assert "IEP-034005 (Lid cart - 60 degree loading)" in report_path.read_text()


class TestExportReport:
    def test_summary_all_ok(self):
        report = ExportReport(
            entries=[
                ExportEntry("a", "a", "a.stl"),
                ExportEntry("b", "b", "b.stl"),
            ]
        )
        assert report.summary() == "Exported 2/2 meshes"
        assert report.failures == []

    def test_summary_with_failures(self):
        report = ExportReport(
            entries=[
                ExportEntry("a", "a", "a.stl"),
                ExportEntry("b", "b", None, status="meshing failed"),
            ]
        )
        assert "1/2" in report.summary()
        assert "1 failed" in report.summary()
        assert len(report.failures) == 1
        assert report.failures[0].name == "b"

    def test_write_report_file(self, tmp_path: Path):
        report = ExportReport(
            entries=[
                ExportEntry("part a", "part_a", "part_a.stl"),
                ExportEntry("part b", "part_b", None, status="no geometry"),
            ]
        )
        report_path = tmp_path / "report.txt"
        report.write(report_path)

        content = report_path.read_text()
        assert "[OK  ] part a" in content
        assert "[FAIL] part b" in content
        assert "no geometry" in content

    def test_exporter_initialization(self):
        exporter = URDFExporter()
        assert exporter.unit_scale == 1.0
        assert exporter.mesh_dir is None
        assert exporter.step_file is None
        assert len(exporter.excluded_assemblies) == 0
        assert exporter.report.entries == []
