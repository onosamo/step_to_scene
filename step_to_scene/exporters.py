"""Export functionality for converting assemblies to URDF/XACRO formats.

This module focuses on extracting static collision geometry from STEP files.
The exported models represent static obstacles/environment that users can
later replace with proper robot descriptions.
"""

import re
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from xml.etree import ElementTree as ET

from step_to_scene.parser import StepAssembly


class Exporter(ABC):
    """Base class for exporters."""

    def __init__(self):
        self.unit_scale = 1.0  # Scale factor to convert to meters
        self.mesh_dir = None  # Directory for mesh files
        self.step_file = None  # Source STEP file path
        self.exported_meshes = (
            set()
        )  # Track already exported meshes to avoid duplicates
        self.assemblies_to_export = set()  # IDs of assemblies that should get STL files
        self.excluded_assemblies = set()  # IDs of assemblies to exclude from STL export
        self.progress_callback = None  # Callback function for progress updates
        self._name_to_shape_map = None  # Cache for name->shape mapping from XCAF
        self._temp_step_file = None  # Temporary STEP file with exclusions applied

    @abstractmethod
    def export(
        self,
        assemblies: list[StepAssembly],
        output_path: Path,
        base_link_name: str = "world",
        unit_scale: float = 1.0,
    ):
        """Export assemblies to the target format.

        Args:
            assemblies: List of assemblies to export
            output_path: Path to write the output file
            base_link_name: Name to use for the base/reference link
            unit_scale: Scale factor to convert units to meters (e.g., 0.001 for mm)
        """
        pass

    def _create_filtered_step_file(self, assemblies_to_export):
        """Create a temporary STEP file with excluded assemblies removed.
        
        Args:
            assemblies_to_export: List of StepAssembly objects to export
            
        Returns:
            Path to temporary STEP file, or None if filtering failed
        """
        if not self.excluded_assemblies or not self.step_file:
            return None
            
        try:
            print(f"  Creating filtered STEP file (excluding {len(self.excluded_assemblies)} assemblies)...")
            
            # Get names of excluded assemblies (including all nested children)
            excluded_names = set()
            
            def collect_excluded_names(assembly_list):
                """Recursively collect names of excluded assemblies."""
                for assembly in assembly_list:
                    if assembly.id in self.excluded_assemblies:
                        excluded_names.add(assembly.name)
                        print(f"    ⊗ Excluding: {assembly.name}")
                    if assembly.children:
                        collect_excluded_names(assembly.children)
            
            # Collect from all assemblies and their children
            collect_excluded_names(assemblies_to_export)
            
            if not excluded_names:
                print("  ⚠ No excluded assembly names found")
                return None
            
            # Read original STEP file
            with open(self.step_file, encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            # Find DATA section
            data_start = content.find('DATA;')
            if data_start == -1:
                print("  ⚠ Could not find DATA section in STEP file")
                return None
            
            header = content[:data_start + 5]
            data_section = content[data_start + 5:]
            
            # Find ENDSEC
            endsec_pos = data_section.find('ENDSEC;')
            if endsec_pos == -1:
                print("  ⚠ Could not find ENDSEC in STEP file")
                return None
                
            footer = data_section[endsec_pos:]
            data_section = data_section[:endsec_pos]
            
            # Parse entities
            entity_pattern = r'(#\d+)\s*=\s*([^;]+);'
            entities = {}
            entity_list = []  # Preserve order
            
            for match in re.finditer(entity_pattern, data_section):
                entity_id = match.group(1)
                entity_data = match.group(2).strip()
                entities[entity_id] = entity_data
                entity_list.append((entity_id, entity_data))
            
            # Find entities to exclude
            excluded_entity_ids = set()
            
            # First pass: find PRODUCT entities with excluded names
            for entity_id, entity_data in entity_list:
                if entity_data.startswith('PRODUCT('):
                    # Extract product name
                    quoted_strings = re.findall(r"'([^']*)'", entity_data)
                    if quoted_strings and quoted_strings[0] in excluded_names:
                        excluded_entity_ids.add(entity_id)
                        print(f"    Marking product entity {entity_id} for exclusion")
            
            # Second pass: find related entities (PRODUCT_DEFINITION_FORMATION, PRODUCT_DEFINITION, etc.)
            # This is a simplified approach - we'll mark entities that reference excluded products
            max_iterations = 10
            for _iteration in range(max_iterations):
                added_count = 0
                for entity_id, entity_data in entity_list:
                    if entity_id in excluded_entity_ids:
                        continue
                    
                    # Check if this entity references any excluded entity
                    refs = re.findall(r'#\d+', entity_data)
                    for ref in refs:
                        if ref in excluded_entity_ids:
                            excluded_entity_ids.add(entity_id)
                            added_count += 1
                            break
                
                if added_count == 0:
                    break
            
            print(f"    Found {len(excluded_entity_ids)} entities to exclude")
            
            # Build filtered data section
            filtered_lines = []
            for entity_id, entity_data in entity_list:
                if entity_id not in excluded_entity_ids:
                    filtered_lines.append(f"{entity_id}={entity_data};")
            
            # Create temporary file
            temp_fd, temp_path = tempfile.mkstemp(suffix='.step', prefix='filtered_')
            temp_file = Path(temp_path)
            
            # Write filtered content
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(header)
                f.write('\n')
                f.write('\n'.join(filtered_lines))
                f.write('\n')
                f.write(footer)
            
            print(f"  ✓ Created filtered STEP file: {temp_file}")
            self._temp_step_file = temp_file
            return temp_file
            
        except Exception as e:
            print(f"  ⚠ Failed to create filtered STEP file: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _cleanup_temp_file(self):
        """Clean up temporary STEP file if it exists."""
        if self._temp_step_file and self._temp_step_file.exists():
            try:
                self._temp_step_file.unlink()
                print(f"  ✓ Cleaned up temporary file: {self._temp_step_file}")
                self._temp_step_file = None
            except Exception as e:
                print(f"  ⚠ Failed to cleanup temporary file: {e}")

    def _build_name_to_shape_map(self, use_filtered_file=False):
        """Build a mapping from assembly names to their shapes using XCAF.

        XCAF (Extended CAD Application Framework) preserves the product structure
        and names from the STEP file, allowing us to correctly map assemblies to their geometry.
        
        Args:
            use_filtered_file: If True, use the temporary filtered STEP file if available
        """
        if self._name_to_shape_map is not None:
            return self._name_to_shape_map

        # Choose which file to use
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

            # Create document
            doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))

            # Create and configure reader
            reader = STEPCAFControl_Reader()
            reader.SetNameMode(True)  # Preserve names
            reader.SetColorMode(True)  # Preserve colors
            reader.SetLayerMode(True)  # Preserve layers

            # Read file
            status = reader.ReadFile(str(step_file_to_read))
            if status != 1:  # IFSelect_RetDone
                print("  ⚠ Failed to read STEP file")
                return {}

            # Transfer to document
            reader.Transfer(doc)

            # Get shape tool
            shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

            # Get root assemblies
            free_labels = TDF_LabelSequence()
            shape_tool.GetFreeShapes(free_labels)

            print(f"  ✓ Found {free_labels.Length()} root assembly/assemblies")

            # Build name->shape mapping by recursively exploring the structure
            name_map = {}
            # Also build name->label mapping for reconstruction
            name_to_label_map = {}

            def get_name_from_label(label):
                """Extract name from XCAF label."""
                name_handle = TDataStd_Name()
                if label.FindAttribute(name_handle.GetID_s(), name_handle):
                    return name_handle.Get().ToExtString()
                return None

            def explore_assembly(label):
                """Recursively explore and map assembly structure."""
                name = get_name_from_label(label)

                # Get shape for this label
                shape = TopoDS_Shape()
                has_shape = shape_tool.GetShape_s(label, shape)

                if name and has_shape and not shape.IsNull():
                    # Store mapping
                    name_map[name] = shape
                    name_to_label_map[name] = label

                # Check for children (components)
                components = TDF_LabelSequence()
                if shape_tool.GetComponents_s(label, components, False):
                    for i in range(1, components.Length() + 1):
                        comp_label = components.Value(i)
                        # Get referenced label (the actual component)
                        ref_label = TDF_Label()
                        if shape_tool.GetReferredShape_s(comp_label, ref_label):
                            explore_assembly(ref_label)

            # Explore all root assemblies
            for i in range(1, free_labels.Length() + 1):
                label = free_labels.Value(i)
                explore_assembly(label)

            print(f"  ✓ Mapped {len(name_map)} assemblies/parts to their geometry")

            self._name_to_shape_map = name_map
            self._name_to_label_map = name_to_label_map  # Store for later use
            self._shape_tool = shape_tool  # Store shape tool
            return name_map

        except Exception as e:
            print(f"  ⚠ Failed to build name-to-shape map: {e}")
            import traceback

            traceback.print_exc()
            return {}

    def _build_shape_excluding_children(self, assembly, excluded_child_names):
        """Build a compound shape for assembly excluding specific children.

        Args:
            assembly: The parent assembly
            excluded_child_names: Set of child assembly names to exclude

        Returns:
            TopoDS_Shape with excluded children removed, or None if failed
        """
        try:
            from OCP.BRep import BRep_Builder
            from OCP.TDataStd import TDataStd_Name
            from OCP.TDF import TDF_Label, TDF_LabelSequence
            from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape

            # Get the label for this assembly
            if not hasattr(self, "_name_to_label_map"):
                print("    ✗ No name_to_label_map found")
                return None

            if assembly.name not in self._name_to_label_map:
                print(f"    ✗ Assembly '{assembly.name}' not in label map")
                return None

            label = self._name_to_label_map[assembly.name]
            shape_tool = self._shape_tool

            # Create a new compound to hold filtered shapes
            builder = BRep_Builder()
            compound = TopoDS_Compound()
            builder.MakeCompound(compound)

            # Get all child components
            components = TDF_LabelSequence()
            has_components = shape_tool.GetComponents_s(label, components, False)

            print(
                f"    ℹ Assembly has {components.Length()} components (has_components={has_components})"
            )

            if not has_components or components.Length() == 0:
                print("    ⚠ No components found, returning None")
                return None

            added_count = 0
            excluded_count = 0

            for i in range(1, components.Length() + 1):
                comp_label = components.Value(i)

                # Get referenced label
                ref_label = TDF_Label()
                if shape_tool.GetReferredShape_s(comp_label, ref_label):
                    # Get name of this child
                    name_handle = TDataStd_Name()
                    child_name = None
                    if ref_label.FindAttribute(name_handle.GetID_s(), name_handle):
                        child_name = name_handle.Get().ToExtString()

                    # Skip if this child is excluded
                    if child_name and child_name in excluded_child_names:
                        print(f"    ⊗ Excluding child: {child_name}")
                        excluded_count += 1
                        continue

                    # Get shape for this child
                    child_shape = TopoDS_Shape()
                    if (
                        shape_tool.GetShape_s(ref_label, child_shape)
                        and not child_shape.IsNull()
                    ):
                        builder.Add(compound, child_shape)
                        added_count += 1
                        if added_count <= 3:
                            print(f"    ✓ Added child: {child_name or 'unnamed'}")

            print(f"    ℹ Total: added {added_count}, excluded {excluded_count}")

            # If compound has no shapes, return None
            if added_count == 0:
                print("    ✗ No shapes added to compound")
                return None

            # Check if compound is valid
            if compound.IsNull():
                print("    ✗ Compound is null")
                return None

            print("    ✓ Built compound successfully")
            return compound

        except Exception as e:
            print(f"  ⚠ Failed to build filtered shape: {e}")
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
        """Export a specific assembly to STL format using correct shape matching.

        Uses XCAF to properly map assembly names to their actual geometry.
        This ensures we export the correct shape for each assembly.

        If the assembly has children that are excluded, builds a filtered compound
        shape without those children.

        Args:
            assembly: Assembly to export (matched by name)
            output_path: Path to write the STL file
            linear_deflection: Linear deflection in mm (higher = coarser/faster, default 1.0)
            angular_deflection: Angular deflection in radians (higher = coarser/faster, default 0.5)

        Returns:
            True if export was successful, False otherwise
        """
        # Check if already exported
        if str(output_path) in self.exported_meshes:
            return True

        try:
            import time

            from OCP.BRepMesh import BRepMesh_IncrementalMesh
            from OCP.StlAPI import StlAPI_Writer

            start_time = time.time()

            # Get the shape for this assembly by name
            name_map = self._build_name_to_shape_map()

            if assembly.name not in name_map:
                print(f"  ⚠ Could not find shape for '{assembly.name}' in STEP file")
                return False

            shape = name_map[assembly.name]

            if shape is None or shape.IsNull():
                print(f"  ⚠ Shape for '{assembly.name}' is null or invalid")
                return False

            # Check if any children are excluded (only if not using filtered STEP file)
            # When using filtered file, exclusions are already handled
            if not self._temp_step_file:
                excluded_child_names = set()
                for child in assembly.children:
                    if child.id in self.excluded_assemblies:
                        excluded_child_names.add(child.name)

                # If there are excluded children, build a filtered shape
                if excluded_child_names:
                    print(
                        f"  ⚙ Building filtered shape for '{assembly.name}' (excluding {len(excluded_child_names)} children)"
                    )
                    filtered_shape = self._build_shape_excluding_children(
                        assembly, excluded_child_names
                    )
                    if filtered_shape and not filtered_shape.IsNull():
                        shape = filtered_shape
                    else:
                        print("  ⚠ Failed to build filtered shape, using original")

            # Mesh the shape with coarse parameters for collision geometry
            # These settings prioritize speed over quality - perfect for physics collision
            # linear_deflection of 1mm and angular of 0.5 rad provide good balance
            mesh = BRepMesh_IncrementalMesh(
                shape,
                linear_deflection,  # 1mm tolerance is fine for collision
                False,  # absolute (not relative)
                angular_deflection,  # ~28° angular tolerance
                True,  # parallel processing - uses all CPU cores
            )
            mesh.Perform()

            if not mesh.IsDone():
                print(f"  ⚠ Meshing failed for {assembly.name}")
                return False

            # Write to STL in binary format (much faster and smaller than ASCII)
            writer = StlAPI_Writer()
            # Binary mode is default; ASCIIMode is a read-only property
            success = writer.Write(shape, str(output_path))

            elapsed = time.time() - start_time

            if success:
                file_size_mb = output_path.stat().st_size / (1024 * 1024)
                print(f"  ✓ {assembly.name} → {file_size_mb:.1f}MB in {elapsed:.1f}s")
                self.exported_meshes.add(str(output_path))
                return True
            else:
                print(f"  ⚠ STL write failed for {assembly.name}")
                return False

        except Exception as e:
            import traceback

            print(f"  ⚠ Export failed for {assembly.name}: {e}")
            traceback.print_exc()
            return False


class URDFExporter(Exporter):
    """Export assemblies to URDF format as static collision geometry.

    All parts are exported as fixed links with collision geometry.
    Users should replace these placeholders with actual robot descriptions.
    """

    def export(
        self,
        assemblies: list[StepAssembly],
        output_path: Path,
        base_link_name: str = "world",
        unit_scale: float = 1.0,
    ):
        """Export assemblies to URDF format as static collision objects.

        Creates separate URDF files for each assembly and a main file that includes all of them.
        Only the selected assemblies get STL meshes exported - nested children don't get separate STL files.
        """
        self.unit_scale = unit_scale

        # Create mesh directory for STL exports
        self.mesh_dir = output_path.parent / f"{output_path.stem}_meshes"
        self.mesh_dir.mkdir(exist_ok=True)

        # Create directory for individual URDF files
        urdf_parts_dir = output_path.parent / f"{output_path.stem}_parts"
        urdf_parts_dir.mkdir(exist_ok=True)

        try:
            # If there are excluded assemblies, create a filtered STEP file
            if self.excluded_assemblies:
                filtered_file = self._create_filtered_step_file(assemblies)
                if filtered_file:
                    print("  ✓ Using filtered STEP file for export")

            # Build name-to-shape mapping (loads STEP file with XCAF to preserve structure)
            print("Loading STEP file for export...")
            file_size_mb = (
                self.step_file.stat().st_size / (1024 * 1024) if self.step_file else 0
            )
            print(f"  File size: {file_size_mb:.1f}MB")

            # Build the mapping - use filtered file if available
            self._build_name_to_shape_map(use_filtered_file=True)

            # Track which assemblies should get STL files (only the top-level selected ones)
            self.assemblies_to_export = set(assembly.id for assembly in assemblies)

            # Count only the selected assemblies (not their children)
            total_count = len(assemblies)
            print(
                f"Processing {total_count} selected assemblies (nested parts will be included but not exported as separate STLs)..."
            )
            self._processed_count = 0

            # Export each top-level assembly to its own URDF file
            included_files = []
            for assembly in assemblies:
                assembly_urdf_path = (
                    urdf_parts_dir / f"{self._sanitize_name(assembly.name)}.urdf"
                )
                self._export_assembly_urdf(assembly, assembly_urdf_path, total_count)
                included_files.append(assembly_urdf_path)

            print(f"✓ Processed all {total_count} selected assemblies")

            # Create main XACRO file that includes all individual URDFs with their transformations
            print("Creating main XACRO file...")
            self._create_main_urdf(
                output_path, assemblies, included_files, urdf_parts_dir, base_link_name
            )
            print(f"✓ Created main XACRO with {len(included_files)} included assemblies")
        
        finally:
            # Clean up temporary file
            self._cleanup_temp_file()

    def _export_assembly_urdf(
        self, assembly: StepAssembly, output_path: Path, total_count: int
    ):
        """Export a single assembly to a URDF file with only its mesh (no nested children).

        Note: The mesh is exported in its local coordinate system without transformations.
        Transformations are applied in the main xacro file's joints.
        """
        # Create root robot element for this assembly
        robot = ET.Element("robot", name=self._sanitize_name(assembly.name))

        # Add comment with description if available
        comment_text = f" URDF for assembly: {assembly.name}. "
        if assembly.description:
            comment_text += f"Description: {assembly.description}. "
        comment_text += "Part of modular URDF export. Contains only this assembly's mesh in local coordinates. "
        comment = ET.Comment(comment_text)
        robot.append(comment)

        # Add only this assembly (not children) - process as top-level
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

        # Export STL mesh for this assembly (unless excluded)
        mesh_file = None
        if self.mesh_dir and self.step_file:
            # Skip STL export if this assembly is excluded
            if assembly.id not in self.excluded_assemblies:
                mesh_filename = f"{link_name}.stl"
                mesh_path = self.mesh_dir / mesh_filename
                if self._export_assembly_to_stl(assembly, mesh_path):
                    # Use relative path from URDF file to mesh
                    mesh_file = f"../{self.mesh_dir.name}/{mesh_filename}"
            else:
                print(f"  ⊗ Skipping STL export for excluded assembly: {assembly.name}")

        # Add collision element (no origin - mesh is in local coordinates)
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

        # Add visual element (no origin - mesh is in local coordinates)
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

        # Add inertial element
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

        # Pretty print XML
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
        """Create main XACRO file that includes all individual assembly URDFs with transformations.

        Uses XACRO format because standard URDF doesn't support file includes.
        Applies assembly transformations to the fixed joints connecting each assembly to the world.
        """
        # Create with XACRO namespace
        robot = ET.Element(
            "robot",
            name="static_environment",
            attrib={"xmlns:xacro": "http://www.ros.org/wiki/xacro"},
        )

        # Add comment explaining the structure
        unit_info = (
            f"Units converted to meters (scale factor: {self.unit_scale})"
            if self.unit_scale != 1.0
            else "Units in meters"
        )
        comment = ET.Comment(
            f" Main XACRO file for static collision geometry. "
            f"{unit_info}. "
            f"This file includes {len(included_files)} separate assembly URDF files using xacro:include. "
            f"Each assembly is defined in its own file in the '{parts_dir.name}' directory. "
            f"Transformations from the STEP file are applied to the fixed joints. "
            f"To use: xacro {output_path.name} > output.urdf "
        )
        robot.append(comment)

        # Add the base/world link
        ET.SubElement(robot, "link", name=base_link_name)

        # Include all assembly URDF files using xacro:include and apply transformations
        for urdf_file, assembly in zip(included_files, assemblies, strict=False):
            # Use relative path from main URDF to parts directory
            relative_path = f"{parts_dir.name}/{urdf_file.name}"
            assembly_name = self._sanitize_name(assembly.name)

            # Add comment for readability (with description if available)
            comment_text = f" Include {assembly_name} assembly "
            if assembly.description:
                comment_text += f"({assembly.description}) "
            include_comment = ET.Comment(comment_text)
            robot.append(include_comment)

            # Use xacro:include to include the URDF file
            include_elem = ET.SubElement(robot, "xacro:include")
            include_elem.set("filename", relative_path)

            # Create joint connecting world to this assembly with transformation
            joint_name = f"{base_link_name}_to_{assembly_name}_fixed"
            joint = ET.SubElement(robot, "joint", name=joint_name, type="fixed")
            ET.SubElement(joint, "parent", link=base_link_name)
            ET.SubElement(joint, "child", link=assembly_name)

            # Get absolute transformation (from world to this assembly)
            abs_pos, abs_rot = assembly.get_absolute_transform()
            x, y, z = abs_pos
            x *= self.unit_scale
            y *= self.unit_scale
            z *= self.unit_scale

            roll, pitch, yaw = abs_rot

            # Round to 5 decimal places
            x = round(x, 5)
            y = round(y, 5)
            z = round(z, 5)
            roll = round(roll, 5)
            pitch = round(pitch, 5)
            yaw = round(yaw, 5)

            # Only add origin if there's a non-zero transformation
            if (x, y, z) != (0, 0, 0) or (roll, pitch, yaw) != (0, 0, 0):
                ET.SubElement(
                    joint, "origin", xyz=f"{x} {y} {z}", rpy=f"{roll} {pitch} {yaw}"
                )
            else:
                ET.SubElement(joint, "origin", xyz="0 0 0", rpy="0 0 0")

        # Pretty print XML
        self._indent(robot)
        tree = ET.ElementTree(robot)

        # Change extension to .xacro to indicate it's a XACRO file
        if output_path.suffix == ".urdf":
            xacro_path = output_path.with_suffix(".xacro")
        else:
            xacro_path = output_path

        tree.write(xacro_path, encoding="utf-8", xml_declaration=True)

        # Also create a note file explaining how to use it
        note_path = output_path.parent / f"{output_path.stem}_README.txt"
        with open(note_path, "w") as f:
            f.write(f"""MODULAR URDF EXPORT WITH TRANSFORMATIONS
==========================================

Generated Files:
- {xacro_path.name} (Main XACRO file - includes all parts with transformations)
- {parts_dir.name}/ (Individual URDF files for each assembly)
- {self.mesh_dir.name}/ (STL mesh files for collision/visual)

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
  - Meshes reference files in {self.mesh_dir.name}/

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

    def _count_assemblies(self, assemblies: list[StepAssembly]) -> int:
        """Count total number of assemblies recursively."""
        count = 0
        for assembly in assemblies:
            count += 1
            count += self._count_assemblies(assembly.children)
        return count

    def _add_assembly_to_urdf(
        self,
        robot: ET.Element,
        assembly: StepAssembly,
        parent_link: str | None,
        total_count: int = 0,
    ):
        """Recursively add assembly and its children to URDF as static collision geometry.

        Only assemblies in self.assemblies_to_export get STL files - children get placeholder geometry.
        """
        # Create link for this assembly
        link_name = self._sanitize_name(assembly.name)

        # Skip creating joint if this assembly IS the parent (base_link) or no parent provided
        is_base_link = parent_link and (link_name == parent_link)

        # Always create the link
        if not is_base_link:
            # Only increment counter for selected assemblies (top-level)
            if assembly.id in self.assemblies_to_export:
                self._processed_count += 1
                if total_count > 0:
                    msg = f"  [{self._processed_count}/{total_count}] Processing: {assembly.name}"
                    print(msg)
                    if self.progress_callback:
                        self.progress_callback(msg, self._processed_count, total_count)

            link = ET.SubElement(robot, "link", name=link_name)

            # Only export STL mesh if this is a selected assembly (not a nested child) and not excluded
            mesh_file = None
            if (
                assembly.id in self.assemblies_to_export
                and self.mesh_dir
                and self.step_file
            ):
                # Skip STL export if this assembly is excluded
                if assembly.id not in self.excluded_assemblies:
                    mesh_filename = f"{link_name}.stl"
                    mesh_path = self.mesh_dir / mesh_filename
                    if self._export_assembly_to_stl(assembly, mesh_path):
                        # Use relative path from URDF file to mesh
                        mesh_file = f"../{self.mesh_dir.name}/{mesh_filename}"
                else:
                    print(
                        f"  ⊗ Skipping STL export for excluded assembly: {assembly.name}"
                    )

            # Add collision element (primary focus)
            collision = ET.SubElement(link, "collision")
            collision_geometry = ET.SubElement(collision, "geometry")

            if mesh_file:
                # Use exported STL mesh
                mesh_elem = ET.SubElement(collision_geometry, "mesh")
                mesh_elem.set("filename", mesh_file)
                if self.unit_scale != 1.0:
                    # Apply scale if units were converted
                    scale = round(self.unit_scale, 5)
                    mesh_elem.set("scale", f"{scale} {scale} {scale}")
            else:
                # Placeholder collision geometry for nested parts
                ET.SubElement(collision_geometry, "box", size="0.1 0.1 0.1")
                # Add comment for user guidance
                collision_comment = ET.Comment(
                    f" Nested part {assembly.name} - replace with actual mesh or dimensions "
                )
                collision.insert(0, collision_comment)

            # Add visual element (optional, for visualization)
            visual = ET.SubElement(link, "visual")
            visual_geometry = ET.SubElement(visual, "geometry")

            if mesh_file:
                # Use exported STL mesh for visual as well
                mesh_elem = ET.SubElement(visual_geometry, "mesh")
                mesh_elem.set("filename", mesh_file)
                if self.unit_scale != 1.0:
                    scale = round(self.unit_scale, 5)
                    mesh_elem.set("scale", f"{scale} {scale} {scale}")
            else:
                ET.SubElement(visual_geometry, "box", size="0.1 0.1 0.1")

            # Add inertial element for static objects (minimal mass)
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

            # Create fixed joint connecting to parent if parent exists
            if parent_link:
                joint_name = f"{parent_link}_to_{link_name}_fixed"
                joint = ET.SubElement(robot, "joint", name=joint_name, type="fixed")

                # Add comment for user guidance
                joint_comment = ET.Comment(
                    " Update origin based on actual part position from STEP file "
                )
                joint.append(joint_comment)

                ET.SubElement(joint, "parent", link=parent_link)
                ET.SubElement(joint, "child", link=link_name)
                ET.SubElement(joint, "origin", xyz="0 0 0", rpy="0 0 0")

        # Process children
        for child in assembly.children:
            self._add_assembly_to_urdf(robot, child, link_name, total_count)

    def _sanitize_name(self, name: str) -> str:
        """Sanitize name for URDF compliance."""
        # Replace invalid characters with underscores
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        # Ensure it doesn't start with a number
        if sanitized and sanitized[0].isdigit():
            sanitized = f"part_{sanitized}"
        return sanitized or "unnamed_part"

    def _indent(self, elem: ET.Element, level: int = 0):
        """Add indentation to XML for pretty printing."""
        i = "\n" + level * "  "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
            for child in elem:
                self._indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i


def get_exporter(format: str) -> Exporter:
    """Get the appropriate exporter for the given format."""
    exporters = {"urdf": URDFExporter()}

    if format.lower() not in exporters:
        raise ValueError(
            f"Unsupported format: {format}. Supported formats: {list(exporters.keys())}"
        )

    return exporters[format.lower()]


def get_potential_base_links(assemblies: list[StepAssembly]) -> list[StepAssembly]:
    """Get assemblies that could be used as base_link/origin.

    Returns assemblies that:
    - Have names containing 'origin', 'base', 'world', 'root', 'reference', or 'frame'
    - Are marked as potential origins
    """
    potential_origins = []

    def check_assembly(assembly: StepAssembly):
        if assembly.is_origin:
            potential_origins.append(assembly)
        # Also check children recursively
        for child in assembly.children:
            check_assembly(child)

    for assembly in assemblies:
        check_assembly(assembly)

    return potential_origins
