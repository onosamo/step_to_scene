"""End-to-end tests on a generated assembly STEP file with duplicate names,
multi-instance parts, and a reused sub-assembly."""

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from step_to_scene.exporters import URDFExporter
from step_to_scene.geometry import StepGeometry, transform_to_xyz_rpy
from step_to_scene.parser import StepAssembly, StepParser


@pytest.fixture
def parsed(assembly_step_file: Path):
    parser = StepParser(assembly_step_file)
    roots = parser.parse()
    return parser, roots


class TestParserInstanceTree:
    def test_root_and_children(self, parsed):
        _, roots = parsed
        assert len(roots) == 1
        root = roots[0]
        assert root.name == "cell"
        assert len(root.children) == 6

    def test_duplicate_names_are_distinct_products(self, parsed):
        _, roots = parsed
        widgets = [c for c in roots[0].children if c.name == "widget"]
        assert len(widgets) == 3
        assert [w.occurrence_index for w in widgets] == [0, 1, 2]
        # two instances of the box product, one of the sphere product
        product_refs = [w.product_ref for w in widgets]
        assert product_refs[0] == product_refs[1]
        assert product_refs[2] != product_refs[0]

    def test_instances_have_own_transforms(self, parsed):
        _, roots = parsed
        widgets = [c for c in roots[0].children if c.name == "widget"]
        assert widgets[0].position == (0.0, 0.0, 0.0)
        assert widgets[1].position == (100.0, 0.0, 0.0)
        assert widgets[2].position == (200.0, 0.0, 0.0)

    def test_reused_subassembly_children_not_shared(self, parsed):
        _, roots = parsed
        subasms = [c for c in roots[0].children if c.name == "subasm"]
        assert len(subasms) == 2
        first_pins = [c for c in subasms[0].children if c.name == "pin"]
        second_pins = [c for c in subasms[1].children if c.name == "pin"]
        assert len(first_pins) == 1
        assert len(second_pins) == 1
        assert first_pins[0] is not second_pins[0]
        assert first_pins[0].parent is subasms[0]
        assert second_pins[0].parent is subasms[1]
        assert first_pins[0].id != second_pins[0].id

    def test_all_instance_ids_unique(self, parsed):
        _, roots = parsed
        seen: set[str] = set()

        def walk(node: StepAssembly):
            assert node.id not in seen, f"duplicate instance id {node.id}"
            seen.add(node.id)
            for child in node.children:
                walk(child)

        for root in roots:
            walk(root)


class TestGeometryMatching:
    def test_every_parser_node_matches(self, assembly_step_file: Path, parsed):
        _, roots = parsed
        geometry = StepGeometry(assembly_step_file)
        geometry.load()

        def walk(node: StepAssembly):
            instance = geometry.find(tuple(node.name_path()))
            assert instance is not None, f"no CAD match for {node.name_path()}"
            for child in node.children:
                walk(child)

        for root in roots:
            walk(root)

    def test_same_name_distinct_products_resolve_to_distinct_shapes(
        self, assembly_step_file: Path, parsed
    ):
        _, roots = parsed
        geometry = StepGeometry(assembly_step_file)
        geometry.load()

        widgets = [c for c in roots[0].children if c.name == "widget"]
        keys = [geometry.find(tuple(w.name_path())).product_key for w in widgets]
        assert keys[0] == keys[1], "same product should share geometry"
        assert keys[2] != keys[0], "distinct product must not share geometry"

    def test_absolute_transforms(self, assembly_step_file: Path, parsed):
        _, roots = parsed
        geometry = StepGeometry(assembly_step_file)
        geometry.load()

        subasms = [c for c in roots[0].children if c.name == "subasm"]
        pin_path = tuple(subasms[1].children[0].name_path())
        instance = geometry.find(pin_path)
        xyz, _ = transform_to_xyz_rpy(instance.absolute_transform)
        # subasm #2 at z=200, pin at local z=3
        assert xyz[2] == pytest.approx(203.0, abs=1e-6)


class TestExport:
    @pytest.fixture
    def exported(self, assembly_step_file: Path, parsed, tmp_path: Path):
        parser, roots = parsed
        exporter = URDFExporter()
        exporter.step_file = assembly_step_file
        output = tmp_path / "scene.xacro"
        report = exporter.export(
            list(roots[0].children),
            output,
            unit_scale=parser.get_unit_info()[1],
        )
        return report, output, tmp_path

    def test_all_meshes_exported(self, exported):
        report, _, _ = exported
        assert len(report.entries) == 6
        assert report.failures == []

    def test_unique_link_names(self, exported):
        report, _, _ = exported
        link_names = [entry.link_name for entry in report.entries]
        assert len(link_names) == len(set(link_names))
        assert link_names.count("widget") == 1
        assert "widget_2" in link_names
        assert "widget_3" in link_names

    def test_no_duplicate_links_in_xacro(self, exported):
        from step_to_scene.xml_utils import find_xacro_includes

        _, output, _ = exported
        tree = ET.parse(output)
        root = tree.getroot()

        includes = [elem.get("filename") for elem in find_xacro_includes(root)]
        assert len(includes) == 6
        assert len(set(includes)) == 6, "every include must reference a unique file"

        joints = [joint.get("name") for joint in root.findall("joint")]
        assert len(joints) == len(set(joints)), "joint names must be unique"

        child_links = [
            joint.find("child").get("link") for joint in root.findall("joint")
        ]
        assert len(child_links) == len(set(child_links)), "links must be unique"

    def test_instances_share_stl_distinct_products_do_not(self, exported):
        report, _, _ = exported
        meshes = {entry.link_name: entry.mesh_file for entry in report.entries}
        # both box widget instances share one mesh
        assert meshes["widget"] == meshes["widget_2"]
        # the sphere widget is a different product and must get its own mesh
        assert meshes["widget_3"] != meshes["widget"]
        # both subasm instances share one mesh
        assert meshes["subasm"] == meshes["subasm_2"]

    def test_joint_origins_in_meters(self, exported):
        from step_to_scene.xml_utils import parse_xacro_with_transforms

        _, output, _ = exported
        _, transforms = parse_xacro_with_transforms(output)
        assert transforms["widget_2"]["xyz"] == [0.1, 0.0, 0.0]
        assert transforms["subasm_2"]["xyz"] == [0.0, 0.0, 0.2]

    def test_report_file_written(self, exported):
        _, output, tmp_path = exported
        report_file = tmp_path / f"{output.stem}_export_report.txt"
        assert report_file.exists()
        assert "Exported 6/6 meshes" in report_file.read_text()

    def test_missing_geometry_is_reported_not_silent(
        self, assembly_step_file: Path, tmp_path: Path
    ):
        ghost = StepAssembly("ghost", "#999")
        exporter = URDFExporter()
        exporter.step_file = assembly_step_file
        report = exporter.export([ghost], tmp_path / "scene.xacro")

        assert len(report.failures) == 1
        assert report.failures[0].name == "ghost"
        assert "not found" in report.failures[0].status

        # the part urdf still loads (box placeholder), so the scene stays usable
        part = ET.parse(tmp_path / "scene_parts" / "ghost.urdf")
        assert part.find(".//collision/geometry/box") is not None

    def test_excluded_child_removed_from_mesh(
        self, assembly_step_file: Path, parsed, tmp_path: Path
    ):
        parser, roots = parsed
        subasm = next(c for c in roots[0].children if c.name == "subasm")
        pin = next(c for c in subasm.children if c.name == "pin")

        exporter = URDFExporter()
        exporter.step_file = assembly_step_file
        exporter.excluded_assemblies = {pin.id}
        report = exporter.export([subasm], tmp_path / "scene.xacro")

        assert report.failures == []
        mesh_path = tmp_path / "scene_meshes" / report.entries[0].mesh_file

        import trimesh

        mesh = trimesh.load(mesh_path)
        # plate is 30x30x3; the pin (height 15 at z offset 3) must be gone
        assert mesh.bounds[1][2] <= 3.0 + 1e-3
