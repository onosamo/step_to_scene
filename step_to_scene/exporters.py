"""Export functionality for converting assemblies to URDF/XACRO/SDF formats.

This module focuses on extracting static collision geometry from STEP files.
The exported models represent static obstacles/environment that users can
later replace with proper robot descriptions.
"""

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List
from xml.etree import ElementTree as ET

from step_to_scene.parser import StepAssembly


class Exporter(ABC):
    """Base class for exporters."""

    def __init__(self):
        self.unit_scale = 1.0  # Scale factor to convert to meters

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
        """Export assemblies to URDF format as static collision objects."""
        self.unit_scale = unit_scale
        
        # Create root robot element
        robot = ET.Element("robot", name="static_environment")
        
        # Add comment explaining the purpose and unit conversion
        unit_info = f"Units converted to meters (scale factor: {unit_scale})" if unit_scale != 1.0 else "Units in meters"
        comment = ET.Comment(
            f" This file contains static collision geometry extracted from STEP file. "
            f"{unit_info}. "
            f"Replace robot parts with proper URDF descriptions as needed. "
        )
        robot.append(comment)

        # Add the base/world link (fixed reference frame)
        base_link = ET.SubElement(robot, "link", name=base_link_name)

        # Process each assembly as static collision
        for assembly in assemblies:
            self._add_assembly_to_urdf(robot, assembly, base_link_name)

        # Pretty print XML
        self._indent(robot)
        tree = ET.ElementTree(robot)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)

    def _add_assembly_to_urdf(
        self, robot: ET.Element, assembly: StepAssembly, parent_link: str
    ):
        """Recursively add assembly and its children to URDF as static collision geometry."""
        # Create link for this assembly
        link_name = self._sanitize_name(assembly.name)
        
        # Skip creating joint if this assembly IS the parent (base_link)
        is_base_link = (link_name == parent_link)
        
        # Only create a new link if this is not the base_link (already created)
        if not is_base_link:
            link = ET.SubElement(robot, "link", name=link_name)

            # Add collision element (primary focus)
            collision = ET.SubElement(link, "collision")
            collision_geometry = ET.SubElement(collision, "geometry")
            # Placeholder collision geometry - users will replace with mesh or proper geometry
            ET.SubElement(collision_geometry, "box", size="0.1 0.1 0.1")
            
            # Add comment for user guidance
            collision_comment = ET.Comment(
                f" Replace collision geometry for {assembly.name} with actual mesh or dimensions "
            )
            collision.insert(0, collision_comment)

            # Add visual element (optional, for visualization)
            visual = ET.SubElement(link, "visual")
            visual_geometry = ET.SubElement(visual, "geometry")
            ET.SubElement(visual_geometry, "box", size="0.1 0.1 0.1")

            # Add inertial element for static objects (minimal mass)
            inertial = ET.SubElement(link, "inertial")
            ET.SubElement(inertial, "mass", value="1.0")
            ET.SubElement(inertial, "inertia", ixx="0.01", ixy="0", ixz="0", iyy="0.01", iyz="0", izz="0.01")

            # Create fixed joint connecting to parent (all joints are fixed for static collision)
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
            self._add_assembly_to_urdf(robot, child, link_name)

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

        # Process each assembly
        for assembly in assemblies:
            self._add_assembly_to_xacro(robot, assembly, base_link_name)

        # Pretty print XML
        self._indent(robot)
        tree = ET.ElementTree(robot)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)

    def _add_assembly_to_xacro(
        self, robot: ET.Element, assembly: StepAssembly, parent_link: str
    ):
        """Recursively add assembly and its children to XACRO as static collision geometry."""
        # Create link for this assembly
        link_name = self._sanitize_name(assembly.name)
        
        # Skip if this assembly IS the parent (base_link)
        is_base_link = (link_name == parent_link)
        
        if not is_base_link:
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
            # Use xacro property for size
            ET.SubElement(collision_geometry, "box", size="${default_size} ${default_size} ${default_size}")
            
            # Visual element
            visual = ET.SubElement(link, "visual")
            visual_geometry = ET.SubElement(visual, "geometry")
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
            self._add_assembly_to_xacro(robot, child, link_name)

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
        
        # Create root sdf element
        sdf = ET.Element("sdf", version="1.6")
        
        # Add comment explaining the purpose and unit conversion
        unit_info = f"Units converted to meters (scale factor: {unit_scale})" if unit_scale != 1.0 else "Units in meters"
        comment = ET.Comment(
            f" This file contains static collision geometry extracted from STEP file. "
            f"{unit_info}. "
            f"All models are static (no dynamics). Replace robot parts with proper SDF descriptions. "
        )
        sdf.append(comment)
        
        model = ET.SubElement(sdf, "model", name="static_environment")
        
        # Mark model as static
        ET.SubElement(model, "static").text = "true"

        # Add base/world link
        base_link = ET.SubElement(model, "link", name=base_link_name)

        # Process each assembly
        for assembly in assemblies:
            self._add_assembly_to_sdf(model, assembly, base_link_name)

        # Pretty print XML
        self._indent(sdf)
        tree = ET.ElementTree(sdf)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)

    def _add_assembly_to_sdf(self, model: ET.Element, assembly: StepAssembly, parent_link: str):
        """Recursively add assembly and its children to SDF as static collision geometry."""
        # Create link for this assembly
        link_name = self._sanitize_name(assembly.name)
        
        # Skip if this assembly IS the parent (base_link)
        is_base_link = (link_name == parent_link)
        
        if not is_base_link:
            link = ET.SubElement(model, "link", name=link_name)

            # Add collision element (primary focus)
            collision = ET.SubElement(link, "collision", name=f"{link_name}_collision")
            collision_geometry = ET.SubElement(collision, "geometry")
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
            self._add_assembly_to_sdf(model, child, link_name)

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
