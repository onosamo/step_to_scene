import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from step_to_scene.geometry import (
    GeometryInstance,
    NamePath,
    StepGeometry,
    transform_to_xyz_rpy,
)
from step_to_scene.parser import StepAssembly

# OCCT's XCAF document is always in millimeters (its model unit), whatever
# unit the STEP file declares — shapes and transforms alike.
MM_TO_M = 0.001

_SKIPPED_STATUS = (
    "skipped: all nested geometry is excluded or exported as separate links"
)


@dataclass
class ExportEntry:
    name: str
    link_name: str
    mesh_file: str | None
    status: str = "ok"
    description: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok" or self.skipped

    @property
    def skipped(self) -> bool:
        return self.status.startswith("skipped")


@dataclass
class ExportReport:
    entries: list[ExportEntry] = field(default_factory=list)

    @property
    def failures(self) -> list[ExportEntry]:
        return [entry for entry in self.entries if not entry.ok]

    def summary(self) -> str:
        mesh_count = sum(1 for entry in self.entries if entry.mesh_file)
        summary = f"Exported {mesh_count}/{len(self.entries)} meshes"
        skipped = sum(1 for entry in self.entries if entry.skipped)
        if skipped:
            summary += f", {skipped} links without own geometry"
        if self.failures:
            summary += f", {len(self.failures)} failed"
        return summary

    def write(self, path: Path):
        lines = [self.summary(), ""]
        for entry in self.entries:
            marker = "FAIL" if not entry.ok else "SKIP" if entry.skipped else "OK  "
            mesh = entry.mesh_file or "-"
            name = entry.name
            if entry.description:
                name = f"{entry.name} ({entry.description})"
            lines.append(f"[{marker}] {name} -> link '{entry.link_name}', mesh {mesh}")
            if entry.status != "ok":
                lines.append(f"       reason: {entry.status}")
        path.write_text("\n".join(lines) + "\n")


class Exporter(ABC):
    def __init__(self):
        self.unit_scale = 1.0
        self.mesh_dir: Path | None = None
        self.step_file: Path | None = None
        self.excluded_assemblies: set[str] = set()
        self.progress_callback: Callable[[str], None] | None = None
        self.report = ExportReport()
        self._geometry: StepGeometry | None = None
        self._reset_run_state()

    def _reset_run_state(self):
        """State accumulated during one export() run."""
        self.report = ExportReport()
        self._link_names: set[str] = set()
        self._mesh_names: set[str] = set()
        self._stl_cache: dict[tuple, tuple[str | None, str]] = {}
        self._instances: dict[str, GeometryInstance | None] = {}

    @abstractmethod
    def export(
        self,
        assemblies: list[StepAssembly],
        output_path: Path,
        base_link_name: str = "world",
        unit_scale: float = 1.0,
    ) -> ExportReport:
        pass

    def _load_geometry(self) -> StepGeometry | None:
        if self._geometry is not None:
            return self._geometry
        if not self.step_file or not self.step_file.exists():
            return None

        try:
            geometry = StepGeometry.for_file(self.step_file)
            geometry.load(progress_callback=self.progress_callback)
        except Exception as e:
            print(f"  Failed to load CAD geometry: {e}")
            import traceback

            traceback.print_exc()
            return None

        self._geometry = geometry
        return geometry

    def _resolve_instance(self, assembly: StepAssembly) -> GeometryInstance | None:
        """The assembly's CAD instance, resolved once per export run."""
        if assembly.id not in self._instances:
            geometry = self._load_geometry()
            instance = (
                geometry.find(assembly.name_path()) if geometry is not None else None
            )
            self._instances[assembly.id] = instance
        return self._instances[assembly.id]

    def _allocate_link_name(self, name: str) -> str:
        return _unique_name(self._sanitize_name(name), self._link_names)

    def _excluded_paths_under(self, assembly: StepAssembly) -> set[NamePath]:
        """Name paths (relative to ``assembly``) of excluded descendants."""
        excluded: set[NamePath] = set()
        if not self.excluded_assemblies:
            return excluded

        def walk(node: StepAssembly, prefix: NamePath):
            for child in node.children:
                child_path = prefix + ((child.name, child.occurrence_index),)
                if child.id in self.excluded_assemblies:
                    excluded.add(child_path)
                else:
                    walk(child, child_path)

        walk(assembly, ())
        return excluded

    def _export_mesh(
        self, assembly: StepAssembly, link_name: str
    ) -> tuple[str | None, str]:
        """Write the assembly's STL. Returns (mesh filename, status)."""
        geometry = self._load_geometry()
        if geometry is None or self.mesh_dir is None:
            return None, "CAD geometry unavailable"

        instance = self._resolve_instance(assembly)
        if instance is None:
            return None, "not found in CAD document (product may have no geometry)"

        excluded_paths = self._excluded_paths_under(assembly)

        # Instances of the same product share one STL; assemblies with
        # exclusions get their own since their geometry is instance-specific.
        cache_key = (instance.product_key, tuple(sorted(excluded_paths)))
        if cache_key in self._stl_cache:
            return self._stl_cache[cache_key]

        if excluded_paths:
            shape = geometry.shape_excluding(instance, excluded_paths)
            if shape is None or shape.IsNull():
                # Everything below this assembly is excluded — typically
                # because its children are selected and exported as their own
                # links. An empty link is the correct result, not a failure.
                self._stl_cache[cache_key] = (None, _SKIPPED_STATUS)
                return None, _SKIPPED_STATUS
            filename_base = _unique_name(link_name, self._mesh_names)
        else:
            shape = geometry.shape_for(instance)
            filename_base = _unique_name(
                self._sanitize_name(instance.name), self._mesh_names
            )

        mesh_filename = f"{filename_base}.stl"
        mesh_path = self.mesh_dir / mesh_filename

        start_time = time.time()
        ok, reason = StepGeometry.write_stl(shape, mesh_path)
        if not ok:
            self._stl_cache[cache_key] = (None, reason)
            return None, reason

        elapsed = time.time() - start_time
        file_size_mb = mesh_path.stat().st_size / (1024 * 1024)
        print(
            f"  {assembly.name} -> {mesh_filename} ({file_size_mb:.1f}MB in {elapsed:.1f}s)"
        )

        self._stl_cache[cache_key] = (mesh_filename, "ok")
        return mesh_filename, "ok"

    def _sanitize_name(self, name: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"part_{sanitized}"
        return sanitized or "unnamed_part"


def _unique_name(base: str, used: set[str]) -> str:
    """Reserve ``base`` in ``used``, suffixing with _2, _3, ... on collision."""
    name = base
    suffix = 1
    while name in used:
        suffix += 1
        name = f"{base}_{suffix}"
    used.add(name)
    return name


def _comment_safe(text: str) -> str:
    """Make free text legal inside an XML comment.

    ElementTree serializes comments verbatim, and '--' (or a trailing '-')
    produces a non-well-formed document.
    """
    while "--" in text:
        text = text.replace("--", "- -")
    if text.endswith("-"):
        text += " "
    return text


class URDFExporter(Exporter):
    def __init__(self):
        super().__init__()
        self._processed_count = 0

    def export(
        self,
        assemblies: list[StepAssembly],
        output_path: Path,
        base_link_name: str = "world",
        unit_scale: float = 1.0,
    ) -> ExportReport:
        self.unit_scale = unit_scale
        self._reset_run_state()
        self.mesh_dir = output_path.parent / f"{output_path.stem}_meshes"
        self.mesh_dir.mkdir(exist_ok=True)

        urdf_parts_dir = output_path.parent / f"{output_path.stem}_parts"
        urdf_parts_dir.mkdir(exist_ok=True)

        print("Loading STEP file for export...")
        file_size_mb = (
            self.step_file.stat().st_size / (1024 * 1024) if self.step_file else 0
        )
        print(f"  File size: {file_size_mb:.1f}MB")

        self._load_geometry()

        total_count = len(assemblies)
        print(f"Processing {total_count} selected assemblies...")
        self._processed_count = 0

        exported: list[tuple[StepAssembly, str, Path]] = []
        for assembly in assemblies:
            link_name = self._allocate_link_name(assembly.name)
            assembly_urdf_path = urdf_parts_dir / f"{link_name}.urdf"
            self._export_assembly_urdf(
                assembly, link_name, assembly_urdf_path, total_count
            )
            exported.append((assembly, link_name, assembly_urdf_path))

        print(f"Processed all {total_count} selected assemblies")
        print(self.report.summary())

        print("Creating main XACRO file...")
        self._create_main_urdf(output_path, exported, urdf_parts_dir, base_link_name)
        print(f"Created main XACRO with {len(exported)} included assemblies")

        report_path = output_path.parent / f"{output_path.stem}_export_report.txt"
        self.report.write(report_path)
        print(f"Export report: {report_path}")

        return self.report

    def _export_assembly_urdf(
        self,
        assembly: StepAssembly,
        link_name: str,
        output_path: Path,
        total_count: int,
    ):
        robot = ET.Element("robot", name=link_name)

        self._processed_count += 1
        if total_count > 0:
            msg = (
                f"  [{self._processed_count}/{total_count}] Processing: {assembly.name}"
            )
            print(msg)
            if self.progress_callback:
                self.progress_callback(msg)

        link = ET.SubElement(robot, "link", name=link_name)

        mesh_file, status = self._export_mesh(assembly, link_name)
        self.report.entries.append(
            ExportEntry(
                name=assembly.name,
                link_name=link_name,
                mesh_file=mesh_file,
                status=status,
                description=assembly.description,
            )
        )
        skipped = status.startswith("skipped")
        if mesh_file is None and not skipped:
            print(f"  No mesh for '{assembly.name}': {status}")
        mesh_ref = (
            f"../{self.mesh_dir.name}/{mesh_file}"
            if mesh_file and self.mesh_dir
            else None
        )

        # A link whose geometry lives entirely in separately exported child
        # links stays empty; a placeholder box is emitted only for failures
        # so the problem is visible in the scene as well as in the report.
        if mesh_ref or not skipped:
            for section in ("collision", "visual"):
                geometry_parent = ET.SubElement(link, section)
                geometry_elem = ET.SubElement(geometry_parent, "geometry")
                if mesh_ref:
                    mesh_elem = ET.SubElement(geometry_elem, "mesh")
                    mesh_elem.set("filename", mesh_ref)
                    # Meshes are tessellated from the OCCT document, which is
                    # in millimeters regardless of the file's declared unit.
                    mesh_elem.set("scale", f"{MM_TO_M} {MM_TO_M} {MM_TO_M}")
                else:
                    ET.SubElement(geometry_elem, "box", size="0.1 0.1 0.1")

        inertial = ET.SubElement(link, "inertial")
        ET.SubElement(inertial, "mass", value="1.0")
        ET.SubElement(
            inertial,
            "inertia",
            ixx="0.01",
            ixy="0",
            ixz="0",
            iyy="0.01",
            iyz="0",
            izz="0.01",
        )

        self._indent(robot)
        tree = ET.ElementTree(robot)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)

    def _assembly_transform(
        self, assembly: StepAssembly
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Placement for the assembly's joint in METERS, preferring exact CAD
        locations.

        The two sources use different units: XCAF transforms are always in
        millimeters (OCCT model units), while the parser fallback is in the
        file's declared unit.
        """
        instance = self._resolve_instance(assembly)
        if instance is not None:
            (x, y, z), rotation = transform_to_xyz_rpy(instance.absolute_transform)
            return (x * MM_TO_M, y * MM_TO_M, z * MM_TO_M), rotation
        (x, y, z), rotation = assembly.get_absolute_transform()
        return (
            x * self.unit_scale,
            y * self.unit_scale,
            z * self.unit_scale,
        ), rotation

    def _create_main_urdf(
        self,
        output_path: Path,
        exported: list[tuple[StepAssembly, str, Path]],
        parts_dir: Path,
        base_link_name: str,
    ):
        robot = ET.Element(
            "robot",
            name="static_environment",
            attrib={"xmlns:xacro": "http://www.ros.org/wiki/xacro"},
        )

        ET.SubElement(robot, "link", name=base_link_name)

        for assembly, link_name, urdf_file in exported:
            relative_path = f"{parts_dir.name}/{urdf_file.name}"

            if assembly.description:
                comment_text = (
                    f" Include {link_name} assembly ({assembly.description}) "
                )
            else:
                comment_text = f" Include {link_name} assembly "
            robot.append(ET.Comment(_comment_safe(comment_text)))

            include_elem = ET.SubElement(robot, "xacro:include")
            include_elem.set("filename", relative_path)

            joint_name = f"{base_link_name}_to_{link_name}_fixed"
            joint = ET.SubElement(robot, "joint", name=joint_name, type="fixed")
            ET.SubElement(joint, "parent", link=base_link_name)
            ET.SubElement(joint, "child", link=link_name)

            (x, y, z), abs_rot = self._assembly_transform(assembly)
            roll, pitch, yaw = abs_rot

            x = round(x, 5)
            y = round(y, 5)
            z = round(z, 5)
            roll = round(roll, 5)
            pitch = round(pitch, 5)
            yaw = round(yaw, 5)

            ET.SubElement(
                joint, "origin", xyz=f"{x} {y} {z}", rpy=f"{roll} {pitch} {yaw}"
            )

        self._indent(robot)
        tree = ET.ElementTree(robot)

        if output_path.suffix == ".urdf":
            xacro_path = output_path.with_suffix(".xacro")
        else:
            xacro_path = output_path

        tree.write(xacro_path, encoding="utf-8", xml_declaration=True)

        note_path = output_path.parent / f"{output_path.stem}_README.txt"
        mesh_dir_name = self.mesh_dir.name if self.mesh_dir else "meshes"
        stl_count = len({e.mesh_file for e in self.report.entries if e.mesh_file})
        note_content = f"""MODULAR URDF EXPORT WITH TRANSFORMATIONS
==========================================

Generated Files:
- {xacro_path.name} (Main XACRO file - includes all parts with transformations)
- {parts_dir.name}/ (Individual URDF files for each assembly)
- {mesh_dir_name}/ (STL mesh files for collision/visual)

Usage:
------

This export uses XACRO format for the main file to enable modular includes.
Placements from the STEP file are applied to the fixed joints in the main XACRO.

To convert to URDF:
  xacro {xacro_path.name} > output.urdf

Or use directly in ROS launch files:
  <param name="robot_description" command="xacro {xacro_path.name}"/>

Structure:
----------

{xacro_path.name}:
  - Defines world/base link
  - Includes each assembly URDF using <xacro:include>
  - Connects each assembly to world with fixed joints
  - Joints contain the placements (position + rotation) from the STEP file

{parts_dir.name}/*.urdf:
  - Each file contains one link with one mesh (in local coordinates)
  - Every exported instance has a unique link name; instances of the same
    part share one STL mesh
  - Meshes reference files in {mesh_dir_name}/

Placements:
-----------

Placements come from the CAD assembly structure (exact) with the STEP text
parser as fallback.

- Position is scaled from STEP units to meters (scale: {self.unit_scale})
- Rotation is expressed as URDF fixed-axis RPY angles (radians)

Selected Assemblies: {len(exported)}
STL Meshes: {stl_count}
{self.report.summary()}
"""
        note_path.write_text(note_content)

        return xacro_path

    def _indent(self, elem: ET.Element, level: int = 0):
        i = "\n" + level * "  "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
            child = None
            for child in elem:
                self._indent(child, level + 1)
            if child is not None and (not child.tail or not child.tail.strip()):
                child.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i


def get_exporter(format: str) -> Exporter:
    exporters = {"urdf": URDFExporter()}

    if format.lower() not in exporters:
        raise ValueError(
            f"Unsupported format: {format}. Supported formats: {list(exporters.keys())}"
        )

    return exporters[format.lower()]


def get_potential_base_links(assemblies: list[StepAssembly]) -> list[StepAssembly]:
    potential_origins: list[StepAssembly] = []

    def check_assembly(assembly: StepAssembly):
        if assembly.is_origin:
            potential_origins.append(assembly)
        for child in assembly.children:
            check_assembly(child)

    for assembly in assemblies:
        check_assembly(assembly)

    return potential_origins
