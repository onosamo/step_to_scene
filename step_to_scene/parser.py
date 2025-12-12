"""STEP file parser module for extracting assembly structures."""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Keywords used to identify potential origin/base_link assemblies
ORIGIN_KEYWORDS = ['origin', 'base', 'world', 'root', 'reference', 'frame']


class StepAssembly:
    """Represents an assembly or part in a STEP file."""

    def __init__(self, name: str, id: str, parent: Optional["StepAssembly"] = None):
        self.name = name
        self.id = id
        self.parent = parent
        self.children: List[StepAssembly] = []
        self.shape_type = "ASSEMBLY"
        self.position = (0.0, 0.0, 0.0)  # x, y, z position
        self.is_origin = False  # Flag to mark if this can be used as origin/base_link

    def add_child(self, child: "StepAssembly"):
        """Add a child assembly/part."""
        child.parent = self
        self.children.append(child)

    def get_path(self) -> str:
        """Get the full path of this assembly in the hierarchy."""
        if self.parent:
            return f"{self.parent.get_path()}/{self.name}"
        return self.name

    def __repr__(self):
        return f"StepAssembly(name='{self.name}', id='{self.id}', children={len(self.children)})"


class StepParser:
    """Parser for STEP files to extract assembly structure."""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.assemblies: Dict[str, StepAssembly] = {}
        self.root_assemblies: List[StepAssembly] = []
        self.unit_scale = 1.0  # Scale factor to convert to meters (1.0 = meters, 0.001 = mm)
        self.unit_name = "UNKNOWN"

    def parse(self) -> List[StepAssembly]:
        """Parse the STEP file and return root assemblies."""
        try:
            with open(self.filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Extract DATA section
            data_match = re.search(r"DATA;(.*?)ENDSEC;", content, re.DOTALL)
            if not data_match:
                raise ValueError("Could not find DATA section in STEP file")

            data_section = data_match.group(1)

            # Parse entities
            entities = self._parse_entities(data_section)
            
            # Extract unit information
            self._extract_units(entities)

            # Extract assemblies and parts
            self._extract_assemblies(entities)

            return self.root_assemblies

        except Exception as e:
            raise ValueError(f"Failed to parse STEP file: {str(e)}")
    
    def get_unit_info(self) -> Tuple[str, float]:
        """Get the unit name and scale factor for this STEP file."""
        return (self.unit_name, self.unit_scale)

    def _parse_entities(self, data_section: str) -> Dict[str, str]:
        """Parse STEP entities from the data section."""
        entities = {}
        # Match STEP entities: #123=ENTITY_NAME(...)
        pattern = r"(#\d+)\s*=\s*([^;]+);"

        for match in re.finditer(pattern, data_section):
            entity_id = match.group(1)
            entity_data = match.group(2).strip()
            entities[entity_id] = entity_data

        return entities
    
    def _extract_units(self, entities: Dict[str, str]):
        """Extract unit information from STEP file."""
        # Look for SI_UNIT or LENGTH_UNIT entities
        for entity_id, entity_data in entities.items():
            # Check for SI_UNIT with length measure
            if "SI_UNIT" in entity_data and "LENGTH_MEASURE" in entity_data:
                # Check for prefix indicating scale
                if ".MILLI." in entity_data or "'MM'" in entity_data.upper():
                    self.unit_scale = 0.001
                    self.unit_name = "MILLIMETER"
                elif ".CENTI." in entity_data or "'CM'" in entity_data.upper():
                    self.unit_scale = 0.01
                    self.unit_name = "CENTIMETER"
                elif "'M'" in entity_data or ".METRE." in entity_data:
                    self.unit_scale = 1.0
                    self.unit_name = "METER"
                elif "'IN'" in entity_data or "INCH" in entity_data.upper():
                    self.unit_scale = 0.0254
                    self.unit_name = "INCH"
                break
            
            # Alternative: Check for NAMED_UNIT or CONVERSION_BASED_UNIT
            if "LENGTH_MEASURE" in entity_data or "PLANE_ANGLE_MEASURE" in entity_data:
                if "MILLI" in entity_data.upper() or "'MM'" in entity_data.upper():
                    self.unit_scale = 0.001
                    self.unit_name = "MILLIMETER"
                    break

    def _extract_assemblies(self, entities: Dict[str, str]):
        """Extract assembly structure from parsed entities."""
        # Find PRODUCT and PRODUCT_DEFINITION entities
        products = {}
        product_definitions = {}
        shape_representations = {}

        for entity_id, entity_data in entities.items():
            # Extract PRODUCT entities
            if entity_data.startswith("PRODUCT("):
                name_match = re.search(r"PRODUCT\('([^']*)'", entity_data)
                if name_match:
                    name = name_match.group(1)
                    products[entity_id] = name

            # Extract PRODUCT_DEFINITION
            elif entity_data.startswith("PRODUCT_DEFINITION("):
                parts = entity_data.split(",")
                if len(parts) >= 4:
                    # Link to PRODUCT
                    prod_ref = parts[-1].strip().rstrip(")")
                    product_definitions[entity_id] = prod_ref

            # Extract SHAPE_REPRESENTATION
            elif "SHAPE_REPRESENTATION" in entity_data:
                name_match = re.search(r"'([^']*)'", entity_data)
                if name_match:
                    shape_representations[entity_id] = name_match.group(1)

        # Build assembly tree
        # For simplicity, create a flat structure with all found products
        if not products:
            # Create a dummy assembly if no products found
            dummy = StepAssembly("Assembly", "root")
            self.assemblies["root"] = dummy
            self.root_assemblies.append(dummy)
        else:
            for entity_id, name in products.items():
                # Clean up name
                clean_name = name if name else f"Part_{entity_id}"
                assembly = StepAssembly(clean_name, entity_id)
                
                # Mark potential origin parts
                name_lower = clean_name.lower()
                if any(keyword in name_lower for keyword in ORIGIN_KEYWORDS):
                    assembly.is_origin = True
                
                self.assemblies[entity_id] = assembly
                self.root_assemblies.append(assembly)

        # Try to establish parent-child relationships
        # This is a simplified version - full implementation would need more complex parsing
        for entity_id, entity_data in entities.items():
            if "NEXT_ASSEMBLY_USAGE_OCCURRENCE" in entity_data:
                # Extract parent and child references
                refs = re.findall(r"#\d+", entity_data)
                if len(refs) >= 2:
                    parent_ref = refs[0]
                    child_ref = refs[1]
                    if parent_ref in self.assemblies and child_ref in self.assemblies:
                        parent = self.assemblies[parent_ref]
                        child = self.assemblies[child_ref]
                        parent.add_child(child)
                        # Remove child from root if it has a parent
                        if child in self.root_assemblies:
                            self.root_assemblies.remove(child)
