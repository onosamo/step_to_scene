from pathlib import Path

import pytest

from step_to_scene.exporters import (
    URDFExporter,
    _find_excluded_products,
    _parse_step_sections,
    _propagate_exclusions,
    _write_filtered_step,
    get_exporter,
    get_potential_base_links,
)
from step_to_scene.parser import StepAssembly


class TestParseStepSections:
    def test_parse_valid_step_content(self, tmp_path: Path):
        step_content = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('Test'), '2;1');
ENDSEC;
DATA;
#1=PRODUCT('id1','Product1','Description',$);
#2=PRODUCT('id2','Product2','Description',$);
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        result = _parse_step_sections(step_file)

        assert result is not None
        header, entity_list, footer = result
        assert "DATA;" in header
        assert len(entity_list) == 2
        assert entity_list[0][0] == "#1"
        assert "PRODUCT" in entity_list[0][1]
        assert "ENDSEC;" in footer

    def test_parse_missing_data_section(self, tmp_path: Path):
        step_content = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('Test'), '2;1');
ENDSEC;
END-ISO-10303-21;
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        result = _parse_step_sections(step_file)

        assert result is None

    def test_parse_missing_endsec(self, tmp_path: Path):
        step_content = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1=PRODUCT('id1','Product1','Description',$);
"""
        step_file = tmp_path / "test.step"
        step_file.write_text(step_content)

        result = _parse_step_sections(step_file)

        assert result is None


class TestFindExcludedProducts:
    def test_find_excluded_by_name(self):
        entity_list = [
            ("#1", "PRODUCT('id1','PartA','Description',$)"),
            ("#2", "PRODUCT('id2','PartB','Description',$)"),
            ("#3", "PRODUCT('id3','PartC','Description',$)"),
        ]
        excluded_names = {"PartA", "PartC"}

        result = _find_excluded_products(entity_list, excluded_names, match_index=1)

        assert "#1" in result
        assert "#2" not in result
        assert "#3" in result

    def test_find_excluded_empty_names(self):
        entity_list = [
            ("#1", "PRODUCT('id1','PartA','Description',$)"),
        ]
        excluded_names: set[str] = set()

        result = _find_excluded_products(entity_list, excluded_names)

        assert len(result) == 0

    def test_find_excluded_no_match(self):
        entity_list = [
            ("#1", "PRODUCT('id1','PartA','Description',$)"),
        ]
        excluded_names = {"NonExistent"}

        result = _find_excluded_products(entity_list, excluded_names, match_index=1)

        assert len(result) == 0

    def test_find_excluded_match_index_zero(self):
        entity_list = [
            ("#1", "PRODUCT('PartA','name1','Description',$)"),
            ("#2", "PRODUCT('PartB','name2','Description',$)"),
        ]
        excluded_names = {"PartA"}

        result = _find_excluded_products(entity_list, excluded_names, match_index=0)

        assert "#1" in result
        assert "#2" not in result


class TestPropagateExclusions:
    def test_propagate_basic(self):
        entity_list = [
            ("#1", "PRODUCT('id1','Part','Desc',$)"),
            ("#2", "PRODUCT_DEFINITION_FORMATION(#1,'def')"),
            ("#3", "PRODUCT_DEFINITION(#2,'prod_def')"),
        ]
        excluded_ids = {"#1"}

        result = _propagate_exclusions(entity_list, excluded_ids.copy())

        assert "#1" in result
        assert "#2" in result
        assert "#3" in result

    def test_propagate_skips_nauo(self):
        entity_list = [
            ("#1", "PRODUCT('id1','Part','Desc',$)"),
            ("#2", "NEXT_ASSEMBLY_USAGE_OCCURRENCE(#1,'nauo','')"),
        ]
        excluded_ids = {"#1"}

        result = _propagate_exclusions(entity_list, excluded_ids.copy())

        assert "#1" in result
        assert "#2" not in result

    def test_propagate_no_exclusions(self):
        entity_list = [
            ("#1", "PRODUCT('id1','Part','Desc',$)"),
            ("#2", "PRODUCT_DEFINITION_FORMATION(#1,'def')"),
        ]
        excluded_ids: set[str] = set()

        result = _propagate_exclusions(entity_list, excluded_ids)

        assert len(result) == 0


class TestWriteFilteredStep:
    def test_write_filtered_step(self):
        header = "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n"
        entity_list = [
            ("#1", "PRODUCT('id1','Part1','Desc',$)"),
            ("#2", "PRODUCT('id2','Part2','Desc',$)"),
            ("#3", "PRODUCT('id3','Part3','Desc',$)"),
        ]
        footer = "ENDSEC;\nEND-ISO-10303-21;"
        excluded_ids = {"#2"}

        result_path = _write_filtered_step(
            header, entity_list, footer, excluded_ids, "test_"
        )

        try:
            assert result_path.exists()
            content = result_path.read_text()
            assert "#1" in content
            assert "#2" not in content or "#2=" not in content
            assert "#3" in content
            assert "DATA;" in content
            assert "ENDSEC;" in content
        finally:
            result_path.unlink(missing_ok=True)

    def test_write_filtered_step_all_excluded(self):
        header = "DATA;\n"
        entity_list = [
            ("#1", "PRODUCT('id1','Part1','Desc',$)"),
        ]
        footer = "ENDSEC;"
        excluded_ids = {"#1"}

        result_path = _write_filtered_step(header, entity_list, footer, excluded_ids)

        try:
            assert result_path.exists()
            content = result_path.read_text()
            assert "#1=" not in content
        finally:
            result_path.unlink(missing_ok=True)


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


class TestURDFExporter:
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

    def test_exporter_initialization(self):
        exporter = URDFExporter()
        assert exporter.unit_scale == 1.0
        assert exporter.mesh_dir is None
        assert exporter.step_file is None
        assert len(exporter.exported_meshes) == 0
        assert len(exporter.assemblies_to_export) == 0
        assert len(exporter.excluded_assemblies) == 0


class TestExporterBase:
    def test_cleanup_temp_file_nonexistent(self):
        exporter = URDFExporter()
        exporter._temp_step_file = Path("/nonexistent/path/file.step")
        exporter._cleanup_temp_file()

    def test_cleanup_temp_file_exists(self, tmp_path: Path):
        exporter = URDFExporter()
        temp_file = tmp_path / "temp.step"
        temp_file.write_text("test content")
        exporter._temp_step_file = temp_file

        exporter._cleanup_temp_file()

        assert not temp_file.exists()
        assert exporter._temp_step_file is None

    def test_create_filtered_step_no_exclusions(self):
        exporter = URDFExporter()
        exporter.excluded_assemblies = set()

        result = exporter._create_filtered_step_file([])

        assert result is None

    def test_create_filtered_step_no_step_file(self):
        exporter = URDFExporter()
        exporter.excluded_assemblies = {"#1"}
        exporter.step_file = None

        result = exporter._create_filtered_step_file([])

        assert result is None
