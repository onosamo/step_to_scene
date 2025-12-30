import re
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

from step_to_scene.parser import StepAssembly

PROPAGATING_ENTITY_TYPES = frozenset(
    [
        "PRODUCT_DEFINITION_FORMATION",
        "PRODUCT_DEFINITION",
        "PRODUCT_DEFINITION_SHAPE",
        "SHAPE_DEFINITION_REPRESENTATION",
        "SHAPE_REPRESENTATION",
    ]
)


def _parse_step_sections(
    step_file: Path,
) -> tuple[str, list[tuple[str, str]], str] | None:
    with open(step_file, encoding="utf-8", errors="ignore") as f:
        content = f.read()

    data_start = content.find("DATA;")
    if data_start == -1:
        return None

    header = content[: data_start + 5]
    data_section = content[data_start + 5 :]

    endsec_pos = data_section.find("ENDSEC;")
    if endsec_pos == -1:
        return None

    footer = data_section[endsec_pos:]
    data_section = data_section[:endsec_pos]

    entity_pattern = r"(#\d+)\s*=\s*([^;]+);"
    entity_list: list[tuple[str, str]] = []

    for match in re.finditer(entity_pattern, data_section):
        entity_id = match.group(1)
        entity_data = match.group(2).strip()
        entity_list.append((entity_id, entity_data))

    return header, entity_list, footer


def _find_excluded_products(
    entity_list: list[tuple[str, str]],
    excluded_names: set[str],
    match_index: int = 0,
    verbose: bool = False,
) -> set[str]:
    excluded_entity_ids: set[str] = set()

    for entity_id, entity_data in entity_list:
        if entity_data.startswith("PRODUCT("):
            quoted_strings = re.findall(r"'([^']*)'", entity_data)
            if (
                len(quoted_strings) > match_index
                and quoted_strings[match_index] in excluded_names
            ):
                excluded_entity_ids.add(entity_id)
                if verbose:
                    print(f"    Excluding product: {quoted_strings[match_index]}")

    return excluded_entity_ids


def _propagate_exclusions(
    entity_list: list[tuple[str, str]],
    excluded_entity_ids: set[str],
    max_iterations: int = 5,
) -> set[str]:
    for _ in range(max_iterations):
        added_count = 0
        for entity_id, entity_data in entity_list:
            if entity_id in excluded_entity_ids:
                continue

            entity_type = entity_data.split("(")[0] if "(" in entity_data else ""

            if "NEXT_ASSEMBLY_USAGE_OCCURRENCE" in entity_type:
                continue

            if entity_type in PROPAGATING_ENTITY_TYPES:
                refs = re.findall(r"#\d+", entity_data)
                for ref in refs:
                    if ref in excluded_entity_ids:
                        excluded_entity_ids.add(entity_id)
                        added_count += 1
                        break

        if added_count == 0:
            break

    return excluded_entity_ids


def _write_filtered_step(
    header: str,
    entity_list: list[tuple[str, str]],
    footer: str,
    excluded_entity_ids: set[str],
    prefix: str = "filtered_",
) -> Path:
    filtered_lines = []
    for entity_id, entity_data in entity_list:
        if entity_id not in excluded_entity_ids:
            filtered_lines.append(f"{entity_id}={entity_data};")

    _, temp_path = tempfile.mkstemp(suffix=".step", prefix=prefix)
    temp_file = Path(temp_path)

    with open(temp_file, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n")
        f.write("\n".join(filtered_lines))
        f.write("\n")
        f.write(footer)

    return temp_file


class Exporter(ABC):
    def __init__(self):
        self.unit_scale = 1.0
        self.mesh_dir: Path | None = None
        self.step_file: Path | None = None
        self.exported_meshes: set[str] = set()
        self.assemblies_to_export: set[str] = set()
        self.excluded_assemblies: set[str] = set()
        self.progress_callback: Callable | None = None
        self._name_to_shape_map: dict | None = None
        self._temp_step_file: Path | None = None

    @abstractmethod
    def export(
        self,
        assemblies: list[StepAssembly],
        output_path: Path,
        base_link_name: str = "world",
        unit_scale: float = 1.0,
    ):
        pass

    def _create_filtered_step_file(
        self, assemblies_to_export: list[StepAssembly]
    ) -> Path | None:
        if not self.excluded_assemblies or not self.step_file:
            return None

        try:
            print(
                f"  Creating filtered STEP file "
                f"(excluding {len(self.excluded_assemblies)} assemblies)..."
            )

            excluded_names: set[str] = set()

            def collect_excluded_names(assembly_list: list[StepAssembly]):
                for assembly in assembly_list:
                    if assembly.id in self.excluded_assemblies:
                        excluded_names.add(assembly.name)
                        print(f"    Excluding: {assembly.name}")
                    if assembly.children:
                        collect_excluded_names(assembly.children)

            collect_excluded_names(assemblies_to_export)

            if not excluded_names:
                print("  No excluded assembly names found")
                return None

            parsed = _parse_step_sections(self.step_file)
            if parsed is None:
                print("  Could not parse STEP file sections")
                return None

            header, entity_list, footer = parsed

            excluded_entity_ids = _find_excluded_products(
                entity_list, excluded_names, match_index=0, verbose=True
            )
            excluded_entity_ids = _propagate_exclusions(
                entity_list, excluded_entity_ids
            )

            print(f"    Found {len(excluded_entity_ids)} entities to exclude")

            temp_file = _write_filtered_step(
                header, entity_list, footer, excluded_entity_ids, "filtered_"
            )

            print(f"  Created filtered STEP file: {temp_file}")
            self._temp_step_file = temp_file
            return temp_file

        except Exception as e:
            print(f"  Failed to create filtered STEP file: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _cleanup_temp_file(self):
        if self._temp_step_file and self._temp_step_file.exists():
            try:
                self._temp_step_file.unlink()
                print(f"  Cleaned up temporary file: {self._temp_step_file}")
                self._temp_step_file = None
            except Exception as e:
                print(f"  Failed to cleanup temporary file: {e}")

    def _create_filtered_step_for_assembly(
        self, excluded_child_names: set[str]
    ) -> Path | None:
        if not excluded_child_names or not self.step_file:
            return None

        try:
            parsed = _parse_step_sections(self.step_file)
            if parsed is None:
                return None

            header, entity_list, footer = parsed

            excluded_entity_ids = _find_excluded_products(
                entity_list, excluded_child_names, match_index=1, verbose=True
            )

            if not excluded_entity_ids:
                return None

            excluded_entity_ids = _propagate_exclusions(
                entity_list, excluded_entity_ids
            )

            print(f"    Excluding {len(excluded_entity_ids)} entities total")

            return _write_filtered_step(
                header, entity_list, footer, excluded_entity_ids, "filtered_assembly_"
            )

        except Exception as e:
            print(f"    Failed to create filtered file: {e}")
            return None

    def _build_name_to_shape_map(self, use_filtered_file: bool = False) -> dict:
        if self._name_to_shape_map is not None:
            return self._name_to_shape_map

        step_file_to_read = self.step_file
        if use_filtered_file and self._temp_step_file and self._temp_step_file.exists():
            step_file_to_read = self._temp_step_file
            print(f"  Using filtered STEP file: {step_file_to_read}")

        if not step_file_to_read or not step_file_to_read.exists():
            return {}

        try:
            from OCP.STEPCAFControl import STEPCAFControl_Reader
            from OCP.TCollection import TCollection_ExtendedString
            from OCP.TDataStd import TDataStd_Name
            from OCP.TDF import TDF_Label, TDF_LabelSequence
            from OCP.TDocStd import TDocStd_Document
            from OCP.TopoDS import TopoDS_Shape
            from OCP.XCAFDoc import XCAFDoc_DocumentTool

            print("  Loading STEP file with XCAF (preserves assembly structure)...")

            doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
            reader = STEPCAFControl_Reader()
            reader.SetNameMode(True)
            reader.SetColorMode(True)
            reader.SetLayerMode(True)

            status = reader.ReadFile(str(step_file_to_read))
            if status != 1:
                print("  Failed to read STEP file")
                return {}

            reader.Transfer(doc)
            shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

            free_labels = TDF_LabelSequence()
            shape_tool.GetFreeShapes(free_labels)

            print(f"  Found {free_labels.Length()} root assembly/assemblies")

            name_map: dict = {}
            name_to_label_map: dict = {}

            def get_name_from_label(label: TDF_Label) -> str | None:
                name_handle = TDataStd_Name()
                if label.FindAttribute(name_handle.GetID_s(), name_handle):
                    return name_handle.Get().ToExtString()
                return None

            def explore_assembly(label: TDF_Label):
                name = get_name_from_label(label)
                shape = TopoDS_Shape()
                has_shape = shape_tool.GetShape_s(label, shape)

                if name and has_shape and not shape.IsNull():
                    name_map[name] = shape
                    name_to_label_map[name] = label

                components = TDF_LabelSequence()
                if shape_tool.GetComponents_s(label, components, False):
                    for i in range(1, components.Length() + 1):
                        comp_label = components.Value(i)
                        ref_label = TDF_Label()
                        if shape_tool.GetReferredShape_s(comp_label, ref_label):
                            explore_assembly(ref_label)

            for i in range(1, free_labels.Length() + 1):
                label = free_labels.Value(i)
                explore_assembly(label)

            print(f"  Mapped {len(name_map)} assemblies/parts to their geometry")

            self._name_to_shape_map = name_map
            self._name_to_label_map = name_to_label_map
            self._shape_tool = shape_tool
            return name_map

        except Exception as e:
            print(f"  Failed to build name-to-shape map: {e}")
            import traceback

            traceback.print_exc()
            return {}

    def _build_shape_excluding_children(
        self, assembly: StepAssembly, excluded_child_names: set[str]
    ):
        try:
            from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

            name_map = self._name_to_shape_map
            if not name_map:
                print("    No shape map available")
                return None

            parent_lookup = (
                assembly.product_name
                if hasattr(assembly, "product_name") and assembly.product_name
                else assembly.name
            )
            if parent_lookup not in name_map:
                print(f"    Parent '{parent_lookup}' not in shape map")
                return None

            parent_shape = name_map[parent_lookup]
            if parent_shape.IsNull():
                print("    Parent shape is null")
                return None

            excluded_shapes = []
            for child in assembly.children:
                child_lookup = (
                    child.product_name
                    if hasattr(child, "product_name") and child.product_name
                    else child.name
                )
                if child_lookup in excluded_child_names and child_lookup in name_map:
                    child_shape = name_map[child_lookup]
                    if not child_shape.IsNull():
                        excluded_shapes.append(child_shape)
                        print(f"    Will exclude: {child_lookup}")

            if not excluded_shapes:
                print("    No excluded shapes found in shape map")
                return None

            result_shape = parent_shape
            for i, excluded_shape in enumerate(excluded_shapes):
                try:
                    cut_op = BRepAlgoAPI_Cut(result_shape, excluded_shape)
                    cut_op.Build()
                    if cut_op.IsDone():
                        result_shape = cut_op.Shape()
                        print(f"    Subtracted shape {i + 1}/{len(excluded_shapes)}")
                    else:
                        print(
                            f"    Failed to subtract shape {i + 1}/{len(excluded_shapes)}"
                        )
                except Exception as e:
                    print(f"    Error subtracting shape {i + 1}: {e}")

            if result_shape.IsNull():
                print("    Result shape is null")
                return None

            print(f"    Built filtered shape with {len(excluded_shapes)} exclusions")
            return result_shape

        except Exception as e:
            print(f"  Failed to build filtered shape: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _export_assembly_to_stl(
        self,
        assembly: StepAssembly,
        output_path: Path,
        linear_deflection: float = 1.0,
        angular_deflection: float = 0.5,
    ) -> bool:
        if str(output_path) in self.exported_meshes:
            return True

        try:
            from OCP.BRepMesh import BRepMesh_IncrementalMesh
            from OCP.StlAPI import StlAPI_Writer

            start_time = time.time()
            name_map = self._build_name_to_shape_map()

            lookup_name = (
                assembly.product_name
                if hasattr(assembly, "product_name") and assembly.product_name
                else assembly.name
            )

            if lookup_name not in name_map:
                print(
                    f"  Could not find shape for '{lookup_name}' "
                    f"(assembly: '{assembly.name}') in STEP file"
                )
                return False

            shape = name_map[lookup_name]

            if shape is None or shape.IsNull():
                print(f"  Shape for '{assembly.name}' is null or invalid")
                return False

            excluded_child_names: set[str] = set()
            for child in assembly.children:
                if child.id in self.excluded_assemblies:
                    child_name = (
                        child.product_name
                        if hasattr(child, "product_name") and child.product_name
                        else child.name
                    )
                    excluded_child_names.add(child_name)

            temp_file = None
            if excluded_child_names:
                print(
                    f"  Creating filtered STEP file for '{assembly.name}' "
                    f"(excluding {len(excluded_child_names)} children)"
                )
                temp_file = self._create_filtered_step_for_assembly(
                    excluded_child_names
                )

                if temp_file:
                    print("  Reloading shapes from filtered file...")
                    saved_step_file = self.step_file
                    self.step_file = temp_file
                    self._name_to_shape_map = None

                    name_map = self._build_name_to_shape_map()
                    self.step_file = saved_step_file

                    if lookup_name in name_map:
                        shape = name_map[lookup_name]
                        print("  Using filtered shape")
                    else:
                        print("  Shape not found in filtered file, using original")

                    try:
                        temp_file.unlink()
                        print("  Cleaned up temp file")
                    except Exception as e:
                        print(f"  Failed to clean up temp file: {e}")
                else:
                    print("  Failed to create filtered file, using original shape")

            mesh = BRepMesh_IncrementalMesh(
                shape,
                linear_deflection,
                False,
                angular_deflection,
                True,
            )
            mesh.Perform()

            if not mesh.IsDone():
                print(f"  Meshing failed for {assembly.name}")
                return False

            writer = StlAPI_Writer()
            success = writer.Write(shape, str(output_path))

            elapsed = time.time() - start_time

            if success:
                file_size_mb = output_path.stat().st_size / (1024 * 1024)
                print(f"  {assembly.name} -> {file_size_mb:.1f}MB in {elapsed:.1f}s")
                self.exported_meshes.add(str(output_path))
                return True
            else:
                print(f"  STL write failed for {assembly.name}")
                return False

        except Exception as e:
            import traceback

            print(f"  Export failed for {assembly.name}: {e}")
            traceback.print_exc()
            return False


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
    ):
        self.unit_scale = unit_scale
        self.mesh_dir = output_path.parent / f"{output_path.stem}_meshes"
        self.mesh_dir.mkdir(exist_ok=True)

        urdf_parts_dir = output_path.parent / f"{output_path.stem}_parts"
        urdf_parts_dir.mkdir(exist_ok=True)

        try:
            print("Loading STEP file for export...")
            file_size_mb = (
                self.step_file.stat().st_size / (1024 * 1024) if self.step_file else 0
            )
            print(f"  File size: {file_size_mb:.1f}MB")

            self._build_name_to_shape_map(use_filtered_file=False)
            self.assemblies_to_export = set(assembly.id for assembly in assemblies)

            total_count = len(assemblies)
            print(
                f"Processing {total_count} selected assemblies "
                f"(nested parts will be included but not exported as separate STLs)..."
            )
            self._processed_count = 0

            included_files = []
            for assembly in assemblies:
                assembly_urdf_path = (
                    urdf_parts_dir / f"{self._sanitize_name(assembly.name)}.urdf"
                )
                self._export_assembly_urdf(assembly, assembly_urdf_path, total_count)
                included_files.append(assembly_urdf_path)

            print(f"Processed all {total_count} selected assemblies")

            print("Creating main XACRO file...")
            self._create_main_urdf(
                output_path, assemblies, included_files, urdf_parts_dir, base_link_name
            )
            print(f"Created main XACRO with {len(included_files)} included assemblies")

        finally:
            self._cleanup_temp_file()

    def _export_assembly_urdf(
        self, assembly: StepAssembly, output_path: Path, total_count: int
    ):
        robot = ET.Element("robot", name=self._sanitize_name(assembly.name))

        self._processed_count += 1
        if total_count > 0:
            msg = (
                f"  [{self._processed_count}/{total_count}] Processing: {assembly.name}"
            )
            print(msg)
            if self.progress_callback:
                self.progress_callback(msg, self._processed_count, total_count)

        link_name = self._sanitize_name(assembly.name)
        link = ET.SubElement(robot, "link", name=link_name)

        mesh_file = None
        if self.mesh_dir and self.step_file:
            if assembly.id not in self.excluded_assemblies:
                mesh_filename = f"{link_name}.stl"
                mesh_path = self.mesh_dir / mesh_filename
                if self._export_assembly_to_stl(assembly, mesh_path):
                    mesh_file = f"../{self.mesh_dir.name}/{mesh_filename}"
            else:
                print(f"  Skipping STL export for excluded assembly: {assembly.name}")

        collision = ET.SubElement(link, "collision")
        collision_geometry = ET.SubElement(collision, "geometry")

        if mesh_file:
            mesh_elem = ET.SubElement(collision_geometry, "mesh")
            mesh_elem.set("filename", mesh_file)
            if self.unit_scale != 1.0:
                scale = round(self.unit_scale, 5)
                mesh_elem.set("scale", f"{scale} {scale} {scale}")
        else:
            ET.SubElement(collision_geometry, "box", size="0.1 0.1 0.1")

        visual = ET.SubElement(link, "visual")
        visual_geometry = ET.SubElement(visual, "geometry")

        if mesh_file:
            mesh_elem = ET.SubElement(visual_geometry, "mesh")
            mesh_elem.set("filename", mesh_file)
            if self.unit_scale != 1.0:
                scale = round(self.unit_scale, 5)
                mesh_elem.set("scale", f"{scale} {scale} {scale}")
        else:
            ET.SubElement(visual_geometry, "box", size="0.1 0.1 0.1")

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

    def _create_main_urdf(
        self,
        output_path: Path,
        assemblies: list[StepAssembly],
        included_files: list[Path],
        parts_dir: Path,
        base_link_name: str,
    ):
        robot = ET.Element(
            "robot",
            name="static_environment",
            attrib={"xmlns:xacro": "http://www.ros.org/wiki/xacro"},
        )

        ET.SubElement(robot, "link", name=base_link_name)

        for urdf_file, assembly in zip(included_files, assemblies, strict=False):
            relative_path = f"{parts_dir.name}/{urdf_file.name}"
            assembly_name = self._sanitize_name(assembly.name)

            include_elem = ET.SubElement(robot, "xacro:include")
            include_elem.set("filename", relative_path)

            joint_name = f"{base_link_name}_to_{assembly_name}_fixed"
            joint = ET.SubElement(robot, "joint", name=joint_name, type="fixed")
            ET.SubElement(joint, "parent", link=base_link_name)
            ET.SubElement(joint, "child", link=assembly_name)

            abs_pos, abs_rot = assembly.get_absolute_transform()
            x, y, z = abs_pos
            x *= self.unit_scale
            y *= self.unit_scale
            z *= self.unit_scale

            roll, pitch, yaw = abs_rot

            x = round(x, 5)
            y = round(y, 5)
            z = round(z, 5)
            roll = round(roll, 5)
            pitch = round(pitch, 5)
            yaw = round(yaw, 5)

            if (x, y, z) != (0, 0, 0) or (roll, pitch, yaw) != (0, 0, 0):
                ET.SubElement(
                    joint, "origin", xyz=f"{x} {y} {z}", rpy=f"{roll} {pitch} {yaw}"
                )
            else:
                ET.SubElement(joint, "origin", xyz="0 0 0", rpy="0 0 0")

        self._indent(robot)
        tree = ET.ElementTree(robot)

        if output_path.suffix == ".urdf":
            xacro_path = output_path.with_suffix(".xacro")
        else:
            xacro_path = output_path

        tree.write(xacro_path, encoding="utf-8", xml_declaration=True)

        note_path = output_path.parent / f"{output_path.stem}_README.txt"
        mesh_dir_name = self.mesh_dir.name if self.mesh_dir else "meshes"
        with open(note_path, "w") as f:
            f.write(f"""MODULAR URDF EXPORT WITH TRANSFORMATIONS
==========================================

Generated Files:
- {xacro_path.name} (Main XACRO file - includes all parts with transformations)
- {parts_dir.name}/ (Individual URDF files for each assembly)
- {mesh_dir_name}/ (STL mesh files for collision/visual)

Usage:
------

This export uses XACRO format for the main file to enable modular includes.
Transformations from the STEP file are applied to the fixed joints in the main XACRO.

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
  - Joints contain the transformations (position + rotation) from STEP file

{parts_dir.name}/*.urdf:
  - Each file contains one link with one mesh (in local coordinates)
  - Can be used standalone or via xacro:include
  - Meshes reference files in {mesh_dir_name}/

Transformations:
----------------

Transformations are extracted from the STEP file's ITEM_DEFINED_TRANSFORMATION
entities and applied to the joint origins in the main XACRO file.

- Position is scaled from STEP units to meters (scale: {self.unit_scale})
- Rotation is converted from STEP coordinate system to RPY angles (radians)

Selected Assemblies: {len(included_files)}
STL Meshes: {len(included_files)}
""")

        return xacro_path

    def _sanitize_name(self, name: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"part_{sanitized}"
        return sanitized or "unnamed_part"

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
