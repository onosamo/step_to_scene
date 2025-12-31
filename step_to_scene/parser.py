import math
import re
from pathlib import Path

ORIGIN_KEYWORDS = ["origin", "base", "world", "root", "reference", "frame"]


class StepAssembly:
    def __init__(
        self,
        name: str,
        id: str,
        parent: "StepAssembly | None" = None,
        description: str = "",
        product_name: str | None = None,
    ):
        self.name = name
        self.id = id
        self.description = description
        self.parent = parent
        self.children: list[StepAssembly] = []
        self.shape_type = "ASSEMBLY"
        self.position = (0.0, 0.0, 0.0)
        self.rotation = (0.0, 0.0, 0.0)
        self.transformation_matrix: list[list[float]] | None = None
        self.is_origin = False
        self.product_name = product_name or name
        self.step_entity_id = int(id.lstrip("#")) - 1 if id.startswith("#") else 0

    def add_child(self, child: "StepAssembly"):
        child.parent = self
        self.children.append(child)

    def get_path(self) -> str:
        if self.parent:
            return f"{self.parent.get_path()}/{self.name}"
        return self.name

    def get_absolute_transform(
        self,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        if self.parent is None:
            return self.position, self.rotation

        parent_pos, parent_rot = self.parent.get_absolute_transform()
        return _multiply_transforms(
            parent_pos, parent_rot, self.position, self.rotation
        )

    def __repr__(self):
        return f"StepAssembly(name='{self.name}', id='{self.id}', children={len(self.children)})"


def _multiply_transforms(
    parent_pos: tuple[float, float, float],
    parent_rot: tuple[float, float, float],
    child_pos: tuple[float, float, float],
    child_rot: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    px, py, pz = parent_pos
    pr, pp, py_angle = parent_rot
    cx, cy, cz = child_pos
    cr, cp, cy_child = child_rot

    cos_r, sin_r = math.cos(pr), math.sin(pr)
    cos_p, sin_p = math.cos(pp), math.sin(pp)
    cos_y, sin_y = math.cos(py_angle), math.sin(py_angle)

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

    cos_cr, sin_cr = math.cos(cr), math.sin(cr)
    cos_cp, sin_cp = math.cos(cp), math.sin(cp)
    cos_cy, sin_cy = math.cos(cy_child), math.sin(cy_child)

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
        new_yaw = 0.0

    return (new_x, new_y, new_z), (new_roll, new_pitch, new_yaw)


class StepParser:
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.assemblies: dict[str, StepAssembly] = {}
        self.root_assemblies: list[StepAssembly] = []
        self.unit_scale = 1.0
        self.unit_name = "UNKNOWN"

    def parse(self) -> list[StepAssembly]:
        try:
            with open(self.filepath, encoding="utf-8", errors="ignore") as f:
                content = f.read()

            data_match = re.search(r"DATA;(.*?)ENDSEC;", content, re.DOTALL)
            if not data_match:
                raise ValueError("Could not find DATA section in STEP file")

            data_section = data_match.group(1)
            entities = self._parse_entities(data_section)
            self._extract_units(entities)
            self._extract_assemblies(entities)
            self._extract_transformations(entities)

            return self.root_assemblies

        except Exception as e:
            raise ValueError(f"Failed to parse STEP file: {e}") from e

    def get_unit_info(self) -> tuple[str, float]:
        return (self.unit_name, self.unit_scale)

    def _parse_entities(self, data_section: str) -> dict[str, str]:
        entities = {}
        pattern = r"(#\d+)\s*=\s*([^;]+);"

        for match in re.finditer(pattern, data_section):
            entity_id = match.group(1)
            entity_data = match.group(2).strip()
            entities[entity_id] = entity_data

        return entities

    def _extract_units(self, entities: dict[str, str]):
        for entity_data in entities.values():
            if "SI_UNIT" in entity_data and (
                "LENGTH_MEASURE" in entity_data or "LENGTH_UNIT" in entity_data
            ):
                if ".MILLI." in entity_data or "'MM'" in entity_data.upper():
                    self.unit_scale = 0.001
                    self.unit_name = "MILLIMETER"
                elif ".CENTI." in entity_data or "'CM'" in entity_data.upper():
                    self.unit_scale = 0.01
                    self.unit_name = "CENTIMETER"
                elif (
                    "'M'" in entity_data
                    or ".METRE." in entity_data
                    or "METER" in entity_data.upper()
                ):
                    self.unit_scale = 1.0
                    self.unit_name = "METER"
                elif "'IN'" in entity_data or "INCH" in entity_data.upper():
                    self.unit_scale = 0.0254
                    self.unit_name = "INCH"

                if self.unit_name != "UNKNOWN":
                    break

            if "LENGTH_UNIT" in entity_data or "LENGTH_MEASURE" in entity_data:
                if (
                    "MILLI" in entity_data.upper()
                    or "'MM'" in entity_data.upper()
                    or ".MILLI." in entity_data
                ):
                    self.unit_scale = 0.001
                    self.unit_name = "MILLIMETER"
                    break
                elif (
                    "CENTI" in entity_data.upper()
                    or "'CM'" in entity_data.upper()
                    or ".CENTI." in entity_data
                ):
                    self.unit_scale = 0.01
                    self.unit_name = "CENTIMETER"
                    break

    def _extract_assemblies(self, entities: dict[str, str]):
        products: dict[str, tuple[str, str]] = {}
        product_definitions: dict[str, str] = {}
        product_definition_formations: dict[str, str] = {}

        for entity_id, entity_data in entities.items():
            if entity_data.startswith("PRODUCT("):
                quoted_strings = re.findall(r"'([^']*)'", entity_data)
                if len(quoted_strings) >= 3:
                    name = quoted_strings[1]
                    description = quoted_strings[2]
                    products[entity_id] = (name, description)
                elif len(quoted_strings) >= 2:
                    products[entity_id] = (quoted_strings[1], "")
                elif len(quoted_strings) >= 1:
                    products[entity_id] = (quoted_strings[0], "")

            elif entity_data.startswith("PRODUCT_DEFINITION_FORMATION("):
                refs = re.findall(r"#\d+", entity_data)
                if refs:
                    product_definition_formations[entity_id] = refs[0]

            elif entity_data.startswith("PRODUCT_DEFINITION("):
                refs = re.findall(r"#\d+", entity_data)
                if refs:
                    product_definitions[entity_id] = refs[0]

        prod_def_to_product: dict[str, str] = {}
        for prod_def_id, formation_ref in product_definitions.items():
            if formation_ref in product_definition_formations:
                product_ref = product_definition_formations[formation_ref]
                prod_def_to_product[prod_def_id] = product_ref

        if not products:
            dummy = StepAssembly("Assembly", "root")
            self.assemblies["root"] = dummy
            self.root_assemblies.append(dummy)
        else:
            for entity_id, (name, description) in products.items():
                clean_name = name if name else f"Part_{entity_id}"
                assembly = StepAssembly(clean_name, entity_id, description=description)

                name_lower = clean_name.lower()
                if any(keyword in name_lower for keyword in ORIGIN_KEYWORDS):
                    assembly.is_origin = True

                self.assemblies[entity_id] = assembly
                self.root_assemblies.append(assembly)

        nauo_to_child_product: dict[str, str] = {}
        nauo_to_parent_proddef: dict[str, str] = {}
        proddef_to_nauo: dict[str, list[str]] = {}

        for nauo_id, entity_data in entities.items():
            if "NEXT_ASSEMBLY_USAGE_OCCURRENCE" in entity_data:
                cleaned = re.sub(r"'[^']*'", "''", entity_data)
                refs = re.findall(r"#\d+", cleaned)

                if len(refs) >= 2:
                    parent_prod_def_ref = refs[0]
                    child_prod_def_ref = refs[1]
                    child_ref = prod_def_to_product.get(child_prod_def_ref)

                    if child_ref and child_ref in self.assemblies:
                        nauo_to_child_product[nauo_id] = child_ref
                        nauo_to_parent_proddef[nauo_id] = parent_prod_def_ref

                        if child_prod_def_ref not in proddef_to_nauo:
                            proddef_to_nauo[child_prod_def_ref] = []
                        proddef_to_nauo[child_prod_def_ref].append(nauo_id)

                        child_template = self.assemblies[child_ref]
                        child_instance = StepAssembly(
                            child_template.name,
                            nauo_id,
                            description=child_template.description,
                            product_name=child_template.name,
                        )
                        child_instance.is_origin = child_template.is_origin
                        self.assemblies[nauo_id] = child_instance

        proddef_children: dict[str, list[str]] = {}
        for nauo_id, parent_prod_def_ref in nauo_to_parent_proddef.items():
            if parent_prod_def_ref not in proddef_children:
                proddef_children[parent_prod_def_ref] = []
            proddef_children[parent_prod_def_ref].append(nauo_id)

        for nauo_id, parent_prod_def_ref in nauo_to_parent_proddef.items():
            parent = None

            parent_product_ref = prod_def_to_product.get(parent_prod_def_ref)
            if (
                parent_product_ref
                and parent_product_ref in self.assemblies
                and parent_prod_def_ref not in proddef_to_nauo
            ):
                parent = self.assemblies[parent_product_ref]

            if not parent and parent_prod_def_ref in proddef_to_nauo:
                parent_nauo_ids = proddef_to_nauo[parent_prod_def_ref]

                for parent_nauo_id in parent_nauo_ids:
                    if parent_nauo_id in self.assemblies:
                        parent_asm = self.assemblies[parent_nauo_id]
                        child_instance = self.assemblies[nauo_id]
                        parent_asm.add_child(child_instance)
                        child_instance.parent = parent_asm

                child_product_ref = nauo_to_child_product.get(nauo_id)
                if child_product_ref:
                    child_template = self.assemblies.get(child_product_ref)
                    if child_template and child_template in self.root_assemblies:
                        self.root_assemblies.remove(child_template)
                continue

            if parent and nauo_id in self.assemblies:
                child_instance = self.assemblies[nauo_id]
                parent.add_child(child_instance)

                child_product_ref = nauo_to_child_product.get(nauo_id)
                if child_product_ref:
                    child_template = self.assemblies.get(child_product_ref)
                    if child_template and child_template in self.root_assemblies:
                        self.root_assemblies.remove(child_template)

    def _extract_transformations(self, entities: dict[str, str]):
        nauo_to_transform: dict[str, str] = {}

        for entity_data in entities.values():
            if "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION" in entity_data:
                refs = re.findall(r"#\d+", entity_data)
                if len(refs) >= 2:
                    rep_rel_ref = refs[0]
                    prod_def_shape_ref = refs[1]

                    if rep_rel_ref in entities:
                        rep_rel_data = entities[rep_rel_ref]
                        transform_refs = re.findall(r"#\d+", rep_rel_data)
                        if transform_refs:
                            transform_ref = transform_refs[-1]

                            if prod_def_shape_ref in entities:
                                prod_def_shape_data = entities[prod_def_shape_ref]
                                nauo_refs = re.findall(r"#\d+", prod_def_shape_data)
                                if nauo_refs:
                                    nauo_to_transform[nauo_refs[0]] = transform_ref

        for nauo_ref, transform_ref in nauo_to_transform.items():
            if transform_ref not in entities:
                continue

            transform_data = entities[transform_ref]
            if "ITEM_DEFINED_TRANSFORMATION" not in transform_data:
                continue

            axis_refs = re.findall(r"#\d+", transform_data)
            if len(axis_refs) < 2:
                continue

            target_axis_ref = axis_refs[1]
            position, z_dir, x_dir = self._parse_axis2_placement_3d(
                target_axis_ref, entities
            )

            if position is None:
                continue

            rotation = self._calculate_rpy_from_axes(x_dir, z_dir)

            if nauo_ref in self.assemblies:
                assembly = self.assemblies[nauo_ref]
                assembly.position = position
                assembly.rotation = rotation
            elif nauo_ref in entities:
                nauo_data = entities[nauo_ref]
                cleaned = re.sub(r"'[^']*'", "''", nauo_data)
                refs = re.findall(r"#\d+", cleaned)
                if len(refs) >= 2:
                    child_prod_def_ref = refs[1]

                    for assembly_id, assembly in self.assemblies.items():
                        if self._assembly_matches_product_def(
                            assembly_id, child_prod_def_ref, entities
                        ):
                            assembly.position = position
                            assembly.rotation = rotation
                            break

    def _parse_axis2_placement_3d(
        self, axis_ref: str, entities: dict[str, str]
    ) -> tuple[
        tuple[float, float, float] | None,
        tuple[float, float, float] | None,
        tuple[float, float, float] | None,
    ]:
        if axis_ref not in entities:
            return None, None, None

        axis_data = entities[axis_ref]
        if "AXIS2_PLACEMENT_3D" not in axis_data:
            return None, None, None

        refs = re.findall(r"#\d+", axis_data)
        if len(refs) < 3:
            return None, None, None

        point_ref, z_dir_ref, x_dir_ref = refs[0], refs[1], refs[2]

        position = self._parse_cartesian_point(point_ref, entities)
        z_dir = self._parse_direction(z_dir_ref, entities)
        x_dir = self._parse_direction(x_dir_ref, entities)

        return position, z_dir, x_dir

    def _parse_cartesian_point(
        self, ref: str, entities: dict[str, str]
    ) -> tuple[float, float, float] | None:
        if ref not in entities:
            return None
        data = entities[ref]
        coords_match = re.search(r",\(([^)]+)\)", data)
        if coords_match:
            try:
                coords = [float(x.strip()) for x in coords_match.group(1).split(",")]
                if len(coords) == 3:
                    return (coords[0], coords[1], coords[2])
            except ValueError:
                pass
        return None

    def _parse_direction(
        self, ref: str, entities: dict[str, str]
    ) -> tuple[float, float, float] | None:
        if ref not in entities:
            return None
        data = entities[ref]
        dir_match = re.search(r",\(([^)]+)\)", data)
        if dir_match:
            try:
                vals = [float(x.strip()) for x in dir_match.group(1).split(",")]
                if len(vals) == 3:
                    return (vals[0], vals[1], vals[2])
            except ValueError:
                pass
        return None

    def _calculate_rpy_from_axes(
        self,
        x_axis: tuple[float, float, float] | None,
        z_axis: tuple[float, float, float] | None,
    ) -> tuple[float, float, float]:
        if x_axis is None or z_axis is None:
            return (0.0, 0.0, 0.0)

        x = self._normalize_vector(x_axis)
        z = self._normalize_vector(z_axis)

        y = (
            z[1] * x[2] - z[2] * x[1],
            z[2] * x[0] - z[0] * x[2],
            z[0] * x[1] - z[1] * x[0],
        )
        y = self._normalize_vector(y)

        sy = math.sqrt(x[0] * x[0] + x[1] * x[1])
        singular = sy < 1e-6

        if not singular:
            roll = math.atan2(y[2], z[2])
            pitch = math.atan2(-x[2], sy)
            yaw = math.atan2(x[1], x[0])
        else:
            roll = math.atan2(-z[1], y[1])
            pitch = math.atan2(-x[2], sy)
            yaw = 0.0

        return (roll, pitch, yaw)

    def _normalize_vector(
        self, v: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        mag = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        if mag < 1e-10:
            return (1.0, 0.0, 0.0)
        return (v[0] / mag, v[1] / mag, v[2] / mag)

    def _assembly_matches_product_def(
        self, assembly_id: str, prod_def_ref: str, entities: dict[str, str]
    ) -> bool:
        if prod_def_ref not in entities:
            return False

        prod_def_data = entities[prod_def_ref]
        if "PRODUCT_DEFINITION" not in prod_def_data:
            return False

        refs = re.findall(r"#\d+", prod_def_data)
        if not refs:
            return False

        formation_ref = refs[0]
        if formation_ref not in entities:
            return False

        formation_data = entities[formation_ref]
        if "PRODUCT_DEFINITION_FORMATION" not in formation_data:
            return False

        product_refs = re.findall(r"#\d+", formation_data)
        if not product_refs:
            return False

        return product_refs[0] == assembly_id
