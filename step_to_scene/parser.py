"""STEP file parser module for extracting assembly structures."""

import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Keywords used to identify potential origin/base_link assemblies
ORIGIN_KEYWORDS = ['origin', 'base', 'world', 'root', 'reference', 'frame']


class StepAssembly:
    """Represents an assembly or part in a STEP file."""

    def __init__(self, name: str, id: str, parent: Optional["StepAssembly"] = None, description: str = ""):
        self.name = name
        self.id = id
        self.description = description  # Human-readable description from STEP file
        self.parent = parent
        self.children: List[StepAssembly] = []
        self.shape_type = "ASSEMBLY"
        self.position = (0.0, 0.0, 0.0)  # x, y, z position
        self.rotation = (0.0, 0.0, 0.0)  # roll, pitch, yaw in radians
        self.transformation_matrix = None  # 4x4 transformation matrix if available
        self.is_origin = False  # Flag to mark if this can be used as origin/base_link
        
        # Extract numeric entity ID for direct STEP access (e.g., "#123" -> 122)
        # STEP indices are 1-based, but OCC uses 0-based indexing
        self.step_entity_id = int(id.lstrip('#')) - 1 if id.startswith('#') else 0

    def add_child(self, child: "StepAssembly"):
        """Add a child assembly/part."""
        child.parent = self
        self.children.append(child)

    def get_path(self) -> str:
        """Get the full path of this assembly in the hierarchy."""
        if self.parent:
            return f"{self.parent.get_path()}/{self.name}"
        return self.name

    def get_absolute_transform(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        """Get absolute transformation from world/root to this assembly.
        
        Computes the cumulative transformation by multiplying transformations
        from root down to this assembly.
        
        Returns:
            Tuple of (position, rotation) in absolute coordinates
        """
        if self.parent is None:
            return self.position, self.rotation
        
        parent_pos, parent_rot = self.parent.get_absolute_transform()
        
        import math
        
        def multiply_transforms(parent_pos, parent_rot, child_pos, child_rot):
            """Multiply two transforms: result = parent * child."""
            px, py, pz = parent_pos
            pr, pp, py_angle = parent_rot
            
            cx, cy, cz = child_pos
            cr, cp, cy_child = child_rot
            
            cos_r = math.cos(pr)
            sin_r = math.sin(pr)
            cos_p = math.cos(pp)
            sin_p = math.sin(pp)
            cos_y = math.cos(py_angle)
            sin_y = math.sin(py_angle)
            
            r11 = cos_y * cos_p
            r12 = cos_y * sin_p * sin_r - sin_y * cos_r
            r13 = cos_y * sin_p * cos_r + sin_y * sin_r
            
            r21 = sin_y * cos_p
            r22 = sin_y * sin_p * sin_r + cos_y * cos_r
            r23 = sin_y * sin_p * cos_r - cos_y * sin_r
            
            r31 = -sin_p
            r32 = cos_p * sin_r
            r33 = cos_p * cos_r
            
            new_x = px + r11 * cx + r12 * cy + r13 * cz
            new_y = py + r21 * cx + r22 * cy + r23 * cz
            new_z = pz + r31 * cx + r32 * cy + r33 * cz
            
            cos_cr = math.cos(cr)
            sin_cr = math.sin(cr)
            cos_cp = math.cos(cp)
            sin_cp = math.sin(cp)
            cos_cy = math.cos(cy_child)
            sin_cy = math.sin(cy_child)
            
            c11 = cos_cy * cos_cp
            c12 = cos_cy * sin_cp * sin_cr - sin_cy * cos_cr
            c13 = cos_cy * sin_cp * cos_cr + sin_cy * sin_cr
            
            c21 = sin_cy * cos_cp
            c22 = sin_cy * sin_cp * sin_cr + cos_cy * cos_cr
            c23 = sin_cy * sin_cp * cos_cr - cos_cy * sin_cr
            
            c31 = -sin_cp
            c32 = cos_cp * sin_cr
            c33 = cos_cp * cos_cr
            
            n11 = r11 * c11 + r12 * c21 + r13 * c31
            n12 = r11 * c12 + r12 * c22 + r13 * c32
            n13 = r11 * c13 + r12 * c23 + r13 * c33
            
            n21 = r21 * c11 + r22 * c21 + r23 * c31
            n22 = r21 * c12 + r22 * c22 + r23 * c32
            n23 = r21 * c13 + r22 * c23 + r23 * c33
            
            n31 = r31 * c11 + r32 * c21 + r33 * c31
            n32 = r31 * c12 + r32 * c22 + r33 * c32
            n33 = r31 * c13 + r32 * c23 + r33 * c33
            
            sy = math.sqrt(n11 * n11 + n21 * n21)
            
            singular = sy < 1e-6
            
            if not singular:
                new_roll = math.atan2(n32, n33)
                new_pitch = math.atan2(-n31, sy)
                new_yaw = math.atan2(n21, n11)
            else:
                new_roll = math.atan2(-n23, n22)
                new_pitch = math.atan2(-n31, sy)
                new_yaw = 0
            
            return (new_x, new_y, new_z), (new_roll, new_pitch, new_yaw)
        
        return multiply_transforms(parent_pos, parent_rot, self.position, self.rotation)

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
            
            # Extract transformations for assemblies
            self._extract_transformations(entities)

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
            # Check for SI_UNIT with length measure or LENGTH_UNIT
            if "SI_UNIT" in entity_data and ("LENGTH_MEASURE" in entity_data or "LENGTH_UNIT" in entity_data):
                # Check for prefix indicating scale
                if ".MILLI." in entity_data or "'MM'" in entity_data.upper():
                    self.unit_scale = 0.001
                    self.unit_name = "MILLIMETER"
                elif ".CENTI." in entity_data or "'CM'" in entity_data.upper():
                    self.unit_scale = 0.01
                    self.unit_name = "CENTIMETER"
                elif "'M'" in entity_data or ".METRE." in entity_data or "METER" in entity_data.upper():
                    self.unit_scale = 1.0
                    self.unit_name = "METER"
                elif "'IN'" in entity_data or "INCH" in entity_data.upper():
                    self.unit_scale = 0.0254
                    self.unit_name = "INCH"
                
                # If we found a unit, stop searching
                if self.unit_name != "UNKNOWN":
                    break
            
            # Alternative: Check for NAMED_UNIT or CONVERSION_BASED_UNIT
            if "LENGTH_UNIT" in entity_data or "LENGTH_MEASURE" in entity_data:
                if "MILLI" in entity_data.upper() or "'MM'" in entity_data.upper() or ".MILLI." in entity_data:
                    self.unit_scale = 0.001
                    self.unit_name = "MILLIMETER"
                    break
                elif "CENTI" in entity_data.upper() or "'CM'" in entity_data.upper() or ".CENTI." in entity_data:
                    self.unit_scale = 0.01
                    self.unit_name = "CENTIMETER"
                    break

    def _extract_assemblies(self, entities: Dict[str, str]):
        """Extract assembly structure from parsed entities."""
        # Find PRODUCT, PRODUCT_DEFINITION, and PRODUCT_DEFINITION_FORMATION entities
        products = {}  # entity_id -> (name, description)
        product_definitions = {}
        product_definition_formations = {}
        shape_representations = {}

        for entity_id, entity_data in entities.items():
            # Extract PRODUCT entities
            # Format: PRODUCT('id','name','description',(#context))
            if entity_data.startswith("PRODUCT("):
                # Extract all quoted strings from PRODUCT
                quoted_strings = re.findall(r"'([^']*)'", entity_data)
                if len(quoted_strings) >= 3:
                    name = quoted_strings[0]  # First quoted string is the ID/name
                    description = quoted_strings[2]  # Third quoted string is the description
                    products[entity_id] = (name, description)
                elif len(quoted_strings) >= 1:
                    # Fallback if description not present
                    name = quoted_strings[0]
                    products[entity_id] = (name, "")

            # Extract PRODUCT_DEFINITION_FORMATION
            elif entity_data.startswith("PRODUCT_DEFINITION_FORMATION("):
                # Format: PRODUCT_DEFINITION_FORMATION('','',#product_ref)
                refs = re.findall(r"#\d+", entity_data)
                if refs:
                    product_ref = refs[0]
                    product_definition_formations[entity_id] = product_ref

            # Extract PRODUCT_DEFINITION
            elif entity_data.startswith("PRODUCT_DEFINITION("):
                # Format: PRODUCT_DEFINITION('design','',#formation_ref,#context_ref)
                refs = re.findall(r"#\d+", entity_data)
                if refs:
                    formation_ref = refs[0]
                    product_definitions[entity_id] = formation_ref

            # Extract SHAPE_REPRESENTATION
            elif "SHAPE_REPRESENTATION" in entity_data:
                name_match = re.search(r"'([^']*)'", entity_data)
                if name_match:
                    shape_representations[entity_id] = name_match.group(1)

        # Build mapping from PRODUCT_DEFINITION to PRODUCT
        prod_def_to_product = {}
        for prod_def_id, formation_ref in product_definitions.items():
            if formation_ref in product_definition_formations:
                product_ref = product_definition_formations[formation_ref]
                prod_def_to_product[prod_def_id] = product_ref

        # Build assembly tree
        # Create assemblies from products and store them by product entity ID
        if not products:
            # Create a dummy assembly if no products found
            dummy = StepAssembly("Assembly", "root")
            self.assemblies["root"] = dummy
            self.root_assemblies.append(dummy)
        else:
            for entity_id, (name, description) in products.items():
                # Clean up name
                clean_name = name if name else f"Part_{entity_id}"
                assembly = StepAssembly(clean_name, entity_id, description=description)
                
                # Mark potential origin parts
                name_lower = clean_name.lower()
                if any(keyword in name_lower for keyword in ORIGIN_KEYWORDS):
                    assembly.is_origin = True
                
                self.assemblies[entity_id] = assembly
                self.root_assemblies.append(assembly)

        # Try to establish parent-child relationships
        for entity_id, entity_data in entities.items():
            if "NEXT_ASSEMBLY_USAGE_OCCURRENCE" in entity_data:
                # NEXT_ASSEMBLY_USAGE_OCCURRENCE format:
                # NEXT_ASSEMBLY_USAGE_OCCURRENCE('id', 'name', 'desc', parent_prod_def_ref, child_prod_def_ref, '')
                # We need to extract parent_prod_def_ref and child_prod_def_ref, skipping quoted values
                
                # Remove everything in quotes first to avoid matching quoted references
                cleaned = re.sub(r"'[^']*'", "''", entity_data)
                # Now find all #references in the cleaned string
                refs = re.findall(r"#\d+", cleaned)
                
                # After removing quoted strings, we should have exactly 2 references: parent and child
                if len(refs) >= 2:
                    parent_prod_def_ref = refs[0]
                    child_prod_def_ref = refs[1]
                    
                    # Map PRODUCT_DEFINITION references to PRODUCT references
                    parent_ref = prod_def_to_product.get(parent_prod_def_ref)
                    child_ref = prod_def_to_product.get(child_prod_def_ref)
                    
                    if parent_ref and child_ref and parent_ref in self.assemblies and child_ref in self.assemblies:
                        parent = self.assemblies[parent_ref]
                        child = self.assemblies[child_ref]
                        parent.add_child(child)
                        # Remove child from root if it has a parent
                        if child in self.root_assemblies:
                            self.root_assemblies.remove(child)
    
    def _extract_transformations(self, entities: Dict[str, str]):
        """Extract transformation matrices from STEP file and assign to assemblies.
        
        Transformations in STEP files are represented by:
        1. CONTEXT_DEPENDENT_SHAPE_REPRESENTATION - links assembly to its transformation
        2. ITEM_DEFINED_TRANSFORMATION - contains source and target AXIS2_PLACEMENT_3D
        3. AXIS2_PLACEMENT_3D - defines position and orientation
        """
        # Build mapping: NEXT_ASSEMBLY_USAGE_OCCURRENCE -> transformation
        nauo_to_transform = {}
        
        for entity_id, entity_data in entities.items():
            if "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION" in entity_data:
                # Extract references to REPRESENTATION_RELATIONSHIP and PRODUCT_DEFINITION_SHAPE
                refs = re.findall(r"#\d+", entity_data)
                if len(refs) >= 2:
                    rep_rel_ref = refs[0]
                    prod_def_shape_ref = refs[1]
                    
                    # Get the REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION
                    if rep_rel_ref in entities:
                        rep_rel_data = entities[rep_rel_ref]
                        # Find ITEM_DEFINED_TRANSFORMATION reference
                        transform_refs = re.findall(r"#\d+", rep_rel_data)
                        # Last reference should be the transformation
                        if transform_refs:
                            transform_ref = transform_refs[-1]
                            
                            # Get PRODUCT_DEFINITION_SHAPE which references NEXT_ASSEMBLY_USAGE_OCCURRENCE
                            if prod_def_shape_ref in entities:
                                prod_def_shape_data = entities[prod_def_shape_ref]
                                nauo_refs = re.findall(r"#\d+", prod_def_shape_data)
                                if nauo_refs:
                                    nauo_ref = nauo_refs[0]
                                    nauo_to_transform[nauo_ref] = transform_ref
        
        # Now parse transformations and assign to assemblies
        for nauo_ref, transform_ref in nauo_to_transform.items():
            if transform_ref not in entities:
                continue
            
            transform_data = entities[transform_ref]
            if "ITEM_DEFINED_TRANSFORMATION" not in transform_data:
                continue
            
            # Extract source and target AXIS2_PLACEMENT_3D references
            # Format: ITEM_DEFINED_TRANSFORMATION('','',#source,#target)
            axis_refs = re.findall(r"#\d+", transform_data)
            if len(axis_refs) < 2:
                continue
            
            source_axis_ref = axis_refs[0]
            target_axis_ref = axis_refs[1]
            
            # Parse target axis placement (this is the transformation we want)
            position, z_dir, x_dir = self._parse_axis2_placement_3d(target_axis_ref, entities)
            
            if position is None:
                continue
            
            # Calculate rotation matrix from direction vectors
            # AXIS2_PLACEMENT_3D provides Z-axis and X-axis directions
            # Y-axis is computed as Z cross X
            rotation = self._calculate_rpy_from_axes(x_dir, z_dir)
            
            # Find which assembly this NAUO refers to and update its transformation
            if nauo_ref in entities:
                nauo_data = entities[nauo_ref]
                # Extract child product definition reference
                cleaned = re.sub(r"'[^']*'", "''", nauo_data)
                refs = re.findall(r"#\d+", cleaned)
                if len(refs) >= 2:
                    child_prod_def_ref = refs[1]
                    
                    # Find the assembly with this product definition
                    for assembly_id, assembly in self.assemblies.items():
                        # Check if this assembly's product definition matches
                        # (we need to trace back through product definitions)
                        if self._assembly_matches_product_def(assembly_id, child_prod_def_ref, entities):
                            assembly.position = position
                            assembly.rotation = rotation
                            break
    
    def _parse_axis2_placement_3d(
        self, axis_ref: str, entities: Dict[str, str]
    ) -> Tuple[Optional[Tuple[float, float, float]], Optional[Tuple[float, float, float]], Optional[Tuple[float, float, float]]]:
        """Parse AXIS2_PLACEMENT_3D to extract position and orientation.
        
        Returns:
            Tuple of (position, z_direction, x_direction) or (None, None, None) if parsing fails
        """
        if axis_ref not in entities:
            return None, None, None
        
        axis_data = entities[axis_ref]
        if "AXIS2_PLACEMENT_3D" not in axis_data:
            return None, None, None
        
        # Extract references to CARTESIAN_POINT and DIRECTION entities
        refs = re.findall(r"#\d+", axis_data)
        if len(refs) < 3:
            return None, None, None
        
        point_ref = refs[0]
        z_dir_ref = refs[1]
        x_dir_ref = refs[2]
        
        # Parse CARTESIAN_POINT
        position = None
        if point_ref in entities:
            point_data = entities[point_ref]
            # Extract coordinates: CARTESIAN_POINT('',(x,y,z))
            # Look for pattern: ,(x,y,z)
            coords_match = re.search(r",\(([^)]+)\)", point_data)
            if coords_match:
                coords_str = coords_match.group(1)
                try:
                    coords = [float(x.strip()) for x in coords_str.split(',')]
                    if len(coords) == 3:
                        position = tuple(coords)
                except ValueError:
                    pass
        
        # Parse Z DIRECTION
        z_dir = None
        if z_dir_ref in entities:
            z_dir_data = entities[z_dir_ref]
            # Extract direction: DIRECTION('',(x,y,z))
            dir_match = re.search(r",\(([^)]+)\)", z_dir_data)
            if dir_match:
                dir_str = dir_match.group(1)
                try:
                    dir_vals = [float(x.strip()) for x in dir_str.split(',')]
                    if len(dir_vals) == 3:
                        z_dir = tuple(dir_vals)
                except ValueError:
                    pass
        
        # Parse X DIRECTION
        x_dir = None
        if x_dir_ref in entities:
            x_dir_data = entities[x_dir_ref]
            # Extract direction: DIRECTION('',(x,y,z))
            dir_match = re.search(r",\(([^)]+)\)", x_dir_data)
            if dir_match:
                dir_str = dir_match.group(1)
                try:
                    dir_vals = [float(x.strip()) for x in dir_str.split(',')]
                    if len(dir_vals) == 3:
                        x_dir = tuple(dir_vals)
                except ValueError:
                    pass
        
        return position, z_dir, x_dir
    
    def _calculate_rpy_from_axes(
        self, x_axis: Tuple[float, float, float], z_axis: Tuple[float, float, float]
    ) -> Tuple[float, float, float]:
        """Calculate roll-pitch-yaw angles from X and Z axis directions.
        
        Given X and Z axes, compute Y = Z cross X, then extract RPY angles.
        
        Returns:
            Tuple of (roll, pitch, yaw) in radians
        """
        if x_axis is None or z_axis is None:
            return (0.0, 0.0, 0.0)
        
        # Normalize axes
        x = self._normalize_vector(x_axis)
        z = self._normalize_vector(z_axis)
        
        # Compute Y axis as Z cross X
        y = (
            z[1] * x[2] - z[2] * x[1],
            z[2] * x[0] - z[0] * x[2],
            z[0] * x[1] - z[1] * x[0]
        )
        y = self._normalize_vector(y)
        
        # Build rotation matrix [X Y Z] as columns
        # R = [x[0] y[0] z[0]]
        #     [x[1] y[1] z[1]]
        #     [x[2] y[2] z[2]]
        
        # Extract RPY from rotation matrix using XYZ convention
        # pitch = atan2(-R[2,0], sqrt(R[0,0]^2 + R[1,0]^2))
        # yaw = atan2(R[1,0], R[0,0])
        # roll = atan2(R[2,1], R[2,2])
        
        sy = math.sqrt(x[0] * x[0] + x[1] * x[1])
        
        singular = sy < 1e-6
        
        if not singular:
            roll = math.atan2(y[2], z[2])
            pitch = math.atan2(-x[2], sy)
            yaw = math.atan2(x[1], x[0])
        else:
            roll = math.atan2(-z[1], y[1])
            pitch = math.atan2(-x[2], sy)
            yaw = 0
        
        return (roll, pitch, yaw)
    
    def _normalize_vector(self, v: Tuple[float, float, float]) -> Tuple[float, float, float]:
        """Normalize a 3D vector."""
        mag = math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)
        if mag < 1e-10:
            return (1.0, 0.0, 0.0)
        return (v[0]/mag, v[1]/mag, v[2]/mag)
    
    def _assembly_matches_product_def(
        self, assembly_id: str, prod_def_ref: str, entities: Dict[str, str]
    ) -> bool:
        """Check if an assembly's product matches a product definition reference.
        
        Assembly ID is a PRODUCT reference. We need to check if this PRODUCT
        is referenced by the given PRODUCT_DEFINITION through PRODUCT_DEFINITION_FORMATION.
        """
        # Get PRODUCT_DEFINITION entity
        if prod_def_ref not in entities:
            return False
        
        prod_def_data = entities[prod_def_ref]
        if "PRODUCT_DEFINITION" not in prod_def_data:
            return False
        
        # Extract PRODUCT_DEFINITION_FORMATION reference
        refs = re.findall(r"#\d+", prod_def_data)
        if not refs:
            return False
        
        formation_ref = refs[0]
        
        # Get PRODUCT_DEFINITION_FORMATION
        if formation_ref not in entities:
            return False
        
        formation_data = entities[formation_ref]
        if "PRODUCT_DEFINITION_FORMATION" not in formation_data:
            return False
        
        # Extract PRODUCT reference
        product_refs = re.findall(r"#\d+", formation_data)
        if not product_refs:
            return False
        
        product_ref = product_refs[0]
        
        # Check if this matches our assembly ID
        return product_ref == assembly_id
