"""Export functionality for converting assemblies to URDF/XACRO/SDF formats.

This module focuses on extracting static collision geometry from STEP files.
The exported models represent static obstacles/environment that users can
later replace with proper robot descriptions.
"""

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET

import cadquery as cq

from step_to_scene.parser import StepAssembly


class Exporter(ABC):
    """Base class for exporters."""

    def __init__(self):
        self.unit_scale = 1.0  # Scale factor to convert to meters
        self.mesh_dir = None  # Directory for mesh files
        self.step_file = None  # Source STEP file path
        self.exported_meshes = set()  # Track already exported meshes to avoid duplicates
        self._step_solids = None  # Cache for loaded STEP solids
        self._solid_index = 0  # Current solid index for sequential export
        self.assemblies_to_export = set()  # IDs of assemblies that should get STL files

    @abstractmethod
    def export(
        self, 
        assemblies: List[StepAssembly], 
        output_path: Path, 
        base_link_name: str = "world",
        unit_scale: float = 1.0
    ):
        """Export assemblies to the target format.
        
        Args:
            assemblies: List of assemblies to export
            output_path: Path to write the output file
            base_link_name: Name to use for the base/reference link
            unit_scale: Scale factor to convert units to meters (e.g., 0.001 for mm)
        """
        pass

    def _load_step_solids(self) -> List:
        """Load all solids from STEP file once and cache them."""
        if self._step_solids is not None:
            return self._step_solids
        
        if not self.step_file or not self.step_file.exists():
            return []
        
        try:
            result = cq.importers.importStep(str(self.step_file))
            shape = result.val()
            
            # Extract all individual solids from the compound
            if shape.ShapeType() == 'Compound':
                self._step_solids = list(shape.Solids())
            else:
                # Single solid
                self._step_solids = [shape]
                
            print(f"Loaded {len(self._step_solids)} solids from STEP file")
            return self._step_solids
        except Exception as e:
            print(f"Warning: Failed to load STEP solids: {e}")
            return []

    def _export_assembly_to_stl(
        self, 
        assembly: StepAssembly,
        output_path: Path,
        linear_deflection: float = 0.1,
        angular_deflection: float = 0.1
    ) -> bool:
        """Export a specific assembly to STL format.
        
        Uses sequential solid extraction from the STEP file.
        Each assembly gets the next solid in sequence.
        
        Args:
            assembly: Assembly to export
            output_path: Path to write the STL file
            linear_deflection: Linear deflection for mesh generation (smaller = finer)
            angular_deflection: Angular deflection in radians (smaller = finer)
            
        Returns:
            True if export was successful, False otherwise
        """
        # Check if already exported
        if str(output_path) in self.exported_meshes:
            return True
            
        try:
            # Load all solids if not already loaded
            solids = self._load_step_solids()
            
            if not solids:
                return False
            
            # Get the next solid for this assembly
            # Use modulo to handle case where we have more assemblies than solids
            solid = solids[self._solid_index % len(solids)]
            self._solid_index += 1
            
            # Create a workplane with this solid
            wp = cq.Workplane("XY").add(solid)
            
            # Export to STL
            cq.exporters.export(
                wp,
                str(output_path),
                exportType=cq.exporters.ExportTypes.STL,
                tolerance=linear_deflection,
                angularTolerance=angular_deflection
            )
            self.exported_meshes.add(str(output_path))
            return True
        except Exception as e:
            import traceback
            print(f"Warning: Failed to export STL for {assembly.name}: {e}")
            traceback.print_exc()
            return False


class URDFExporter(Exporter):
    """Export assemblies to URDF format as static collision geometry.
    
    All parts are exported as fixed links with collision geometry.
    Users should replace these placeholders with actual robot descriptions.
    """

    def export(
        self, 
        assemblies: List[StepAssembly], 
        output_path: Path,
        base_link_name: str = "world",
        unit_scale: float = 1.0
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
        
        # Load STEP solids once
        print(f"Loading STEP file solids...")
        self._load_step_solids()
        
        # Track which assemblies should get STL files (only the top-level selected ones)
        self.assemblies_to_export = set(assembly.id for assembly in assemblies)
        
        # Count only the selected assemblies (not their children)
        total_count = len(assemblies)
        print(f"Processing {total_count} selected assemblies (nested parts will be included but not exported as separate STLs)...")
        self._processed_count = 0
        
        # Export each top-level assembly to its own URDF file
        included_files = []
        for assembly in assemblies:
            assembly_urdf_path = urdf_parts_dir / f"{self._sanitize_name(assembly.name)}.urdf"
            self._export_assembly_urdf(assembly, assembly_urdf_path, total_count)
            included_files.append(assembly_urdf_path)
        
        print(f"✓ Processed all {total_count} selected assemblies")
        
        # Create main XACRO file that includes all individual URDFs
        print(f"Creating main XACRO file...")
        xacro_path = self._create_main_urdf(output_path, included_files, urdf_parts_dir, base_link_name)
        print(f"✓ Created main XACRO with {len(included_files)} included assemblies")
    
    def _export_assembly_urdf(
        self, 
        assembly: StepAssembly, 
        output_path: Path, 
        total_count: int
    ):
        """Export a single assembly to a URDF file with only its mesh (no nested children)."""
        # Create root robot element for this assembly
        robot = ET.Element("robot", name=self._sanitize_name(assembly.name))
        
        # Add comment
        comment = ET.Comment(
            f" URDF for assembly: {assembly.name}. "
            f"Part of modular URDF export. Contains only this assembly's mesh. "
        )
        robot.append(comment)
        
        # Add only this assembly (not children) - process as top-level
        self._processed_count += 1
        if total_count > 0:
            print(f"  [{self._processed_count}/{total_count}] Processing: {assembly.name}")
        
        link_name = self._sanitize_name(assembly.name)
        link = ET.SubElement(robot, "link", name=link_name)
        
        # Export STL mesh for this assembly
        mesh_file = None
        if self.mesh_dir and self.step_file:
            mesh_filename = f"{link_name}.stl"
            mesh_path = self.mesh_dir / mesh_filename
            if self._export_assembly_to_stl(assembly, mesh_path):
                # Use relative path from URDF file to mesh
                mesh_file = f"../{self.mesh_dir.name}/{mesh_filename}"
        
        # Add collision element
        collision = ET.SubElement(link, "collision")
        collision_geometry = ET.SubElement(collision, "geometry")
        
        if mesh_file:
            mesh_elem = ET.SubElement(collision_geometry, "mesh")
            mesh_elem.set("filename", mesh_file)
            if self.unit_scale != 1.0:
                mesh_elem.set("scale", f"{self.unit_scale} {self.unit_scale} {self.unit_scale}")
        else:
            ET.SubElement(collision_geometry, "box", size="0.1 0.1 0.1")
        
        # Add visual element
        visual = ET.SubElement(link, "visual")
        visual_geometry = ET.SubElement(visual, "geometry")
        
        if mesh_file:
            mesh_elem = ET.SubElement(visual_geometry, "mesh")
            mesh_elem.set("filename", mesh_file)
            if self.unit_scale != 1.0:
                mesh_elem.set("scale", f"{self.unit_scale} {self.unit_scale} {self.unit_scale}")
        else:
            ET.SubElement(visual_geometry, "box", size="0.1 0.1 0.1")
        
        # Add inertial element
        inertial = ET.SubElement(link, "inertial")
        ET.SubElement(inertial, "mass", value="1.0")
        ET.SubElement(inertial, "inertia", ixx="0.01", ixy="0", ixz="0", iyy="0.01", iyz="0", izz="0.01")
        
        # Pretty print XML
        self._indent(robot)
        tree = ET.ElementTree(robot)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
    
    def _create_main_urdf(
        self, 
        output_path: Path, 
        included_files: List[Path], 
        parts_dir: Path,
        base_link_name: str
    ):
        """Create main XACRO file that includes all individual assembly URDFs.
        
        Uses XACRO format because standard URDF doesn't support file includes.
        """
        # Create with XACRO namespace
        robot = ET.Element(
            "robot",
            name="static_environment",
            attrib={"xmlns:xacro": "http://www.ros.org/wiki/xacro"}
        )
        
        # Add comment explaining the structure
        unit_info = f"Units converted to meters (scale factor: {self.unit_scale})" if self.unit_scale != 1.0 else "Units in meters"
        comment = ET.Comment(
            f" Main XACRO file for static collision geometry. "
            f"{unit_info}. "
            f"This file includes {len(included_files)} separate assembly URDF files using xacro:include. "
            f"Each assembly is defined in its own file in the '{parts_dir.name}' directory. "
            f"To use: xacro {output_path.name} > output.urdf "
        )
        robot.append(comment)
        
        # Add the base/world link
        base_link = ET.SubElement(robot, "link", name=base_link_name)
        
        # Include all assembly URDF files using xacro:include
        for urdf_file in included_files:
            # Use relative path from main URDF to parts directory
            relative_path = f"{parts_dir.name}/{urdf_file.name}"
            assembly_name = urdf_file.stem
            
            # Add comment for readability
            include_comment = ET.Comment(f" Include {assembly_name} assembly ")
            robot.append(include_comment)
            
            # Use xacro:include to include the URDF file
            include_elem = ET.SubElement(robot, "xacro:include")
            include_elem.set("filename", relative_path)
            
            # Create joint connecting world to this assembly
            # Note: The link is defined in the included file
            joint_name = f"{base_link_name}_to_{assembly_name}_fixed"
            joint = ET.SubElement(robot, "joint", name=joint_name, type="fixed")
            ET.SubElement(joint, "parent", link=base_link_name)
            ET.SubElement(joint, "child", link=assembly_name)
            ET.SubElement(joint, "origin", xyz="0 0 0", rpy="0 0 0")
        
        # Pretty print XML
        self._indent(robot)
        tree = ET.ElementTree(robot)
        
        # Change extension to .xacro to indicate it's a XACRO file
        if output_path.suffix == '.urdf':
            xacro_path = output_path.with_suffix('.xacro')
        else:
            xacro_path = output_path
        
        tree.write(xacro_path, encoding="utf-8", xml_declaration=True)
        
        # Also create a note file explaining how to use it
        note_path = output_path.parent / f"{output_path.stem}_README.txt"
        with open(note_path, 'w') as f:
            f.write(f"""MODULAR URDF EXPORT
==================

Generated Files:
- {xacro_path.name} (Main XACRO file - includes all parts)
- {parts_dir.name}/ (Individual URDF files for each assembly)
- {self.mesh_dir.name}/ (STL mesh files for collision/visual)

Usage:
------

This export uses XACRO format for the main file to enable modular includes.

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

{parts_dir.name}/*.urdf:
  - Each file contains one link with one mesh
  - Can be used standalone or via xacro:include
  - Meshes reference files in {self.mesh_dir.name}/

Selected Assemblies: {len(included_files)}
STL Meshes: {len(included_files)}
""")
        
        return xacro_path
    
    def _count_assemblies(self, assemblies: List[StepAssembly]) -> int:
        """Count total number of assemblies recursively."""
        count = 0
        for assembly in assemblies:
            count += 1
            count += self._count_assemblies(assembly.children)
        return count

    def _add_assembly_to_urdf(
        self, robot: ET.Element, assembly: StepAssembly, parent_link: Optional[str], total_count: int = 0
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
                    print(f"  [{self._processed_count}/{total_count}] Processing: {assembly.name}")
            
            link = ET.SubElement(robot, "link", name=link_name)

            # Only export STL mesh if this is a selected assembly (not a nested child)
            mesh_file = None
            if assembly.id in self.assemblies_to_export and self.mesh_dir and self.step_file:
                mesh_filename = f"{link_name}.stl"
                mesh_path = self.mesh_dir / mesh_filename
                if self._export_assembly_to_stl(assembly, mesh_path):
                    # Use relative path from URDF file to mesh
                    mesh_file = f"../{self.mesh_dir.name}/{mesh_filename}"

            # Add collision element (primary focus)
            collision = ET.SubElement(link, "collision")
            collision_geometry = ET.SubElement(collision, "geometry")
            
            if mesh_file:
                # Use exported STL mesh
                mesh_elem = ET.SubElement(collision_geometry, "mesh")
                mesh_elem.set("filename", mesh_file)
                if self.unit_scale != 1.0:
                    # Apply scale if units were converted
                    mesh_elem.set("scale", f"{self.unit_scale} {self.unit_scale} {self.unit_scale}")
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
                    mesh_elem.set("scale", f"{self.unit_scale} {self.unit_scale} {self.unit_scale}")
            else:
                ET.SubElement(visual_geometry, "box", size="0.1 0.1 0.1")

            # Add inertial element for static objects (minimal mass)
            inertial = ET.SubElement(link, "inertial")
            ET.SubElement(inertial, "mass", value="1.0")
            ET.SubElement(inertial, "inertia", ixx="0.01", ixy="0", ixz="0", iyy="0.01", iyz="0", izz="0.01")

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


class XACROExporter(Exporter):
    """Export assemblies to XACRO format as static collision geometry.
    
    Uses XACRO macros to make it easier to update collision geometries.
    All parts are exported as fixed links with collision geometry.
    """

    def export(
        self, 
        assemblies: List[StepAssembly], 
        output_path: Path,
        base_link_name: str = "world",
        unit_scale: float = 1.0
    ):
        """Export assemblies to XACRO format as static collision objects."""
        self.unit_scale = unit_scale
        
        # Create mesh directory for STL exports
        self.mesh_dir = output_path.parent / f"{output_path.stem}_meshes"
        self.mesh_dir.mkdir(exist_ok=True)
        
        # Load STEP solids once
        print(f"Loading STEP file solids...")
        self._load_step_solids()
        
        # Create root robot element with xacro namespace
        robot = ET.Element(
            "robot",
            name="static_environment",
            attrib={"xmlns:xacro": "http://www.ros.org/wiki/xacro"},
        )
        
        # Add comment explaining the purpose and unit conversion
        unit_info = f"Units converted to meters (scale factor: {unit_scale})" if unit_scale != 1.0 else "Units in meters"
        comment = ET.Comment(
            f" This file contains static collision geometry extracted from STEP file. "
            f"{unit_info}. "
            f"Individual STL meshes exported for each part. "
            f"Use xacro properties to easily update dimensions and positions. "
            f"Replace robot parts with proper URDF descriptions as needed. "
        )
        robot.append(comment)

        # Add xacro properties for common values
        ET.SubElement(robot, "xacro:property", name="default_mass", value="1.0")
        ET.SubElement(robot, "xacro:property", name="default_size", value="0.1")
        ET.SubElement(robot, "xacro:property", name="unit_scale", value=str(unit_scale))

        # Add the base/world link (fixed reference frame)
        base_link = ET.SubElement(robot, "link", name=base_link_name)

        # Count total assemblies for progress
        total_count = self._count_assemblies(assemblies)
        print(f"Processing {total_count} assemblies...")
        self._processed_count = 0

        # Process each assembly
        for assembly in assemblies:
            self._add_assembly_to_xacro(robot, assembly, base_link_name, total_count)

        print(f"✓ Processed all {total_count} assemblies")

        # Pretty print XML
        self._indent(robot)
        tree = ET.ElementTree(robot)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
    
    def _count_assemblies(self, assemblies: List[StepAssembly]) -> int:
        """Count total number of assemblies recursively."""
        count = 0
        for assembly in assemblies:
            count += 1
            count += self._count_assemblies(assembly.children)
        return count

    def _add_assembly_to_xacro(
        self, robot: ET.Element, assembly: StepAssembly, parent_link: str, total_count: int = 0
    ):
        """Recursively add assembly and its children to XACRO as static collision geometry."""
        # Create link for this assembly
        link_name = self._sanitize_name(assembly.name)
        
        # Skip if this assembly IS the parent (base_link)
        is_base_link = (link_name == parent_link)
        
        if not is_base_link:
            self._processed_count += 1
            if total_count > 0:
                print(f"  [{self._processed_count}/{total_count}] Processing: {assembly.name}")
            
            # Try to export STL mesh for this specific assembly
            mesh_file = None
            if self.mesh_dir and self.step_file:
                mesh_filename = f"{link_name}.stl"
                mesh_path = self.mesh_dir / mesh_filename
                if self._export_assembly_to_stl(assembly, mesh_path):
                    # Use relative path from XACRO file to mesh
                    mesh_file = f"{self.mesh_dir.name}/{mesh_filename}"
            
            # Create macro for reusable collision link
            macro_name = f"{link_name}_collision"
            macro = ET.SubElement(
                robot, 
                "xacro:macro", 
                name=macro_name,
                attrib={"params": "name parent *origin"}
            )
            
            # Link inside macro
            link = ET.SubElement(macro, "link", name="${name}")
            
            # Collision element (primary focus)
            collision = ET.SubElement(link, "collision")
            collision_geometry = ET.SubElement(collision, "geometry")
            
            if mesh_file:
                # Use exported STL mesh
                mesh_elem = ET.SubElement(collision_geometry, "mesh")
                mesh_elem.set("filename", mesh_file)
                if self.unit_scale != 1.0:
                    mesh_elem.set("scale", f"{self.unit_scale} {self.unit_scale} {self.unit_scale}")
            else:
                # Use xacro property for size
                ET.SubElement(collision_geometry, "box", size="${default_size} ${default_size} ${default_size}")
            
            # Visual element
            visual = ET.SubElement(link, "visual")
            visual_geometry = ET.SubElement(visual, "geometry")
            
            if mesh_file:
                # Use exported STL mesh for visual as well
                mesh_elem = ET.SubElement(visual_geometry, "mesh")
                mesh_elem.set("filename", mesh_file)
                if self.unit_scale != 1.0:
                    mesh_elem.set("scale", f"{self.unit_scale} {self.unit_scale} {self.unit_scale}")
            else:
                ET.SubElement(visual_geometry, "box", size="${default_size} ${default_size} ${default_size}")
            
            # Inertial element
            inertial = ET.SubElement(link, "inertial")
            ET.SubElement(inertial, "mass", value="${default_mass}")
            ET.SubElement(inertial, "inertia", ixx="0.01", ixy="0", ixz="0", iyy="0.01", iyz="0", izz="0.01")
            
            # Fixed joint in macro
            joint = ET.SubElement(macro, "joint", name="${name}_fixed", type="fixed")
            ET.SubElement(joint, "parent", link="${parent}")
            ET.SubElement(joint, "child", link="${name}")
            ET.SubElement(joint, "xacro:insert_block", name="origin")

            # Instantiate the macro
            instantiation = ET.SubElement(robot, f"xacro:{macro_name}")
            instantiation.set("name", link_name)
            instantiation.set("parent", parent_link)
            origin_block = ET.SubElement(instantiation, "origin", xyz="0 0 0", rpy="0 0 0")

        # Process children
        for child in assembly.children:
            self._add_assembly_to_xacro(robot, child, link_name, mesh_available, total_count)

    def _sanitize_name(self, name: str) -> str:
        """Sanitize name for XACRO compliance."""
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
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


class SDFExporter(Exporter):
    """Export assemblies to SDF format as static collision geometry.
    
    All parts are exported as static models with collision geometry.
    Suitable for Gazebo simulation environments.
    """

    def export(
        self, 
        assemblies: List[StepAssembly], 
        output_path: Path,
        base_link_name: str = "world",
        unit_scale: float = 1.0
    ):
        """Export assemblies to SDF format as static collision objects."""
        self.unit_scale = unit_scale
        
        # Create mesh directory for STL exports
        self.mesh_dir = output_path.parent / f"{output_path.stem}_meshes"
        self.mesh_dir.mkdir(exist_ok=True)
        
        # Export the entire STEP file as a single STL mesh once
        print(f"Exporting STEP file to STL mesh...")
        master_mesh_path = self.mesh_dir / "complete_assembly.stl"
        mesh_exported = self._export_step_to_stl(master_mesh_path)
        if mesh_exported:
            print(f"✓ Exported complete assembly mesh")
        
        # Create root sdf element
        sdf = ET.Element("sdf", version="1.6")
        
        # Add comment explaining the purpose and unit conversion
        unit_info = f"Units converted to meters (scale factor: {unit_scale})" if unit_scale != 1.0 else "Units in meters"
        comment = ET.Comment(
            f" This file contains static collision geometry extracted from STEP file. "
            f"{unit_info}. "
            f"Complete assembly exported as STL mesh. "
            f"All models are static (no dynamics). Replace robot parts with proper SDF descriptions. "
        )
        sdf.append(comment)
        
        model = ET.SubElement(sdf, "model", name="static_environment")
        
        # Mark model as static
        ET.SubElement(model, "static").text = "true"

        # Add base/world link
        base_link = ET.SubElement(model, "link", name=base_link_name)

        # Count total assemblies for progress
        total_count = self._count_assemblies(assemblies)
        print(f"Processing {total_count} assemblies...")
        self._processed_count = 0

        # Process each assembly
        for assembly in assemblies:
            self._add_assembly_to_sdf(model, assembly, base_link_name, mesh_exported, total_count)

        print(f"✓ Processed all {total_count} assemblies")

        # Pretty print XML
        self._indent(sdf)
        tree = ET.ElementTree(sdf)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
    
    def _count_assemblies(self, assemblies: List[StepAssembly]) -> int:
        """Count total number of assemblies recursively."""
        count = 0
        for assembly in assemblies:
            count += 1
            count += self._count_assemblies(assembly.children)
        return count

    def _add_assembly_to_sdf(self, model: ET.Element, assembly: StepAssembly, parent_link: str, mesh_available: bool = False, total_count: int = 0):
        """Recursively add assembly and its children to SDF as static collision geometry."""
        # Create link for this assembly
        link_name = self._sanitize_name(assembly.name)
        
        # Skip if this assembly IS the parent (base_link)
        is_base_link = (link_name == parent_link)
        
        if not is_base_link:
            self._processed_count += 1
            if total_count > 0:
                print(f"  [{self._processed_count}/{total_count}] Processing: {assembly.name}")
            
            link = ET.SubElement(model, "link", name=link_name)

            # Use the single exported mesh file if available
            mesh_file = None
            if mesh_available and self.mesh_dir:
                # All parts reference the same complete assembly mesh
                mesh_file = f"{self.mesh_dir.name}/complete_assembly.stl"

            # Add collision element (primary focus)
            collision = ET.SubElement(link, "collision", name=f"{link_name}_collision")
            collision_geometry = ET.SubElement(collision, "geometry")
            
            if mesh_file:
                # Use exported STL mesh
                mesh_elem = ET.SubElement(collision_geometry, "mesh")
                ET.SubElement(mesh_elem, "uri").text = f"file://{mesh_file}"
                if self.unit_scale != 1.0:
                    ET.SubElement(mesh_elem, "scale").text = f"{self.unit_scale} {self.unit_scale} {self.unit_scale}"
            else:
                collision_box = ET.SubElement(collision_geometry, "box")
                ET.SubElement(collision_box, "size").text = "0.1 0.1 0.1"
            
            # Add collision properties for static objects
            surface = ET.SubElement(collision, "surface")
            friction = ET.SubElement(surface, "friction")
            ode = ET.SubElement(friction, "ode")
            ET.SubElement(ode, "mu").text = "1.0"
            ET.SubElement(ode, "mu2").text = "1.0"

            # Add visual element
            visual = ET.SubElement(link, "visual", name=f"{link_name}_visual")
            visual_geometry = ET.SubElement(visual, "geometry")
            
            if mesh_file:
                # Use exported STL mesh for visual as well
                mesh_elem = ET.SubElement(visual_geometry, "mesh")
                ET.SubElement(mesh_elem, "uri").text = f"file://{mesh_file}"
                if self.unit_scale != 1.0:
                    ET.SubElement(mesh_elem, "scale").text = f"{self.unit_scale} {self.unit_scale} {self.unit_scale}"
            else:
                visual_box = ET.SubElement(visual_geometry, "box")
                ET.SubElement(visual_box, "size").text = "0.1 0.1 0.1"
            
            # Add material for visualization
            material = ET.SubElement(visual, "material")
            ET.SubElement(material, "ambient").text = "0.5 0.5 0.5 1"
            ET.SubElement(material, "diffuse").text = "0.5 0.5 0.5 1"

            # Add inertial element (minimal for static objects)
            inertial = ET.SubElement(link, "inertial")
            ET.SubElement(inertial, "mass").text = "1.0"
            inertia = ET.SubElement(inertial, "inertia")
            ET.SubElement(inertia, "ixx").text = "0.01"
            ET.SubElement(inertia, "ixy").text = "0"
            ET.SubElement(inertia, "ixz").text = "0"
            ET.SubElement(inertia, "iyy").text = "0.01"
            ET.SubElement(inertia, "iyz").text = "0"
            ET.SubElement(inertia, "izz").text = "0.01"

            # Create fixed joint connecting to parent (all static)
            joint_name = f"{parent_link}_to_{link_name}_fixed"
            joint = ET.SubElement(model, "joint", name=joint_name, type="fixed")
            ET.SubElement(joint, "parent").text = parent_link
            ET.SubElement(joint, "child").text = link_name

            # Add pose (placeholder - update based on actual STEP geometry)
            pose = ET.SubElement(joint, "pose")
            pose.text = "0 0 0 0 0 0"
            pose_comment = ET.Comment(" Update pose based on actual part position from STEP file ")
            joint.insert(0, pose_comment)

        # Process children
        for child in assembly.children:
            self._add_assembly_to_sdf(model, child, link_name, mesh_available, total_count)

    def _sanitize_name(self, name: str) -> str:
        """Sanitize name for SDF compliance."""
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
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
    exporters = {"urdf": URDFExporter(), "xacro": XACROExporter(), "sdf": SDFExporter()}

    if format.lower() not in exporters:
        raise ValueError(f"Unsupported format: {format}. Supported formats: {list(exporters.keys())}")

    return exporters[format.lower()]


def get_potential_base_links(assemblies: List[StepAssembly]) -> List[StepAssembly]:
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
