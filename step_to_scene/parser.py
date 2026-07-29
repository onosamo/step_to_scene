import math
import re
from pathlib import Path

ORIGIN_KEYWORDS = ["origin", "base", "world", "root", "reference", "frame"]

# The parser and the XCAF reading in step_to_scene.geometry are joined by
# name paths: for every node, the path of (product name, occurrence index
# among same-name siblings) pairs from the root. The pieces of that contract
# — the path type, name normalization, and occurrence numbering — are defined
# here, once, and imported by geometry.py.
NamePath = tuple[tuple[str, int], ...]

_X2_RE = re.compile(r"\\X2\\((?:[0-9A-Fa-f]{4})+)\\X0\\")
_X4_RE = re.compile(r"\\X4\\((?:[0-9A-Fa-f]{8})+)\\X0\\")
_X_RE = re.compile(r"\\X\\([0-9A-Fa-f]{2})")
_S_RE = re.compile(r"\\S\\(.)")
_PAGE_RE = re.compile(r"\\P[A-I]\\")


def normalize_step_name(value: str) -> str:
    """Drop physical line wraps that old STEP writers put inside strings.

    Every reading of the file must apply this so names compare equal.
    """
    if "\n" in value or "\r" in value:
        value = value.replace("\r", "").replace("\n", "")
    return value


def assign_occurrence_indices(siblings: list) -> None:
    """Number same-name siblings in order; part of the name-path contract.

    Works on any nodes with ``name`` and ``occurrence_index`` attributes.
    """
    counts: dict[str, int] = {}
    for node in siblings:
        index = counts.get(node.name, 0)
        node.occurrence_index = index
        counts[node.name] = index + 1


def matrix_to_rpy(
    r11: float,
    r21: float,
    r31: float,
    r32: float,
    r33: float,
    r22: float,
    r23: float,
) -> tuple[float, float, float]:
    """Rotation-matrix elements -> URDF fixed-axis roll/pitch/yaw (radians)."""
    sy = math.sqrt(r11 * r11 + r21 * r21)
    if sy > 1e-6:
        roll = math.atan2(r32, r33)
        pitch = math.atan2(-r31, sy)
        yaw = math.atan2(r21, r11)
    else:
        roll = math.atan2(-r23, r22)
        pitch = math.atan2(-r31, sy)
        yaw = 0.0
    return (roll, pitch, yaw)


def _decode_step_string(value: str) -> str:
    """Decode ISO 10303-21 string escapes (e.g. ``\\X\\FC`` -> ``ü``).

    CAD readers like OCCT decode these, so the names extracted from the STEP
    text must be decoded the same way to line up with them.
    """
    value = normalize_step_name(value)
    if "\\" not in value and "''" not in value:
        return value
    value = value.replace("''", "'")
    value = _X2_RE.sub(
        lambda m: "".join(
            chr(int(m.group(1)[i : i + 4], 16)) for i in range(0, len(m.group(1)), 4)
        ),
        value,
    )
    value = _X4_RE.sub(
        lambda m: "".join(
            chr(int(m.group(1)[i : i + 8], 16)) for i in range(0, len(m.group(1)), 8)
        ),
        value,
    )
    value = _X_RE.sub(lambda m: chr(int(m.group(1), 16)), value)
    value = _S_RE.sub(lambda m: chr(ord(m.group(1)) + 128), value)
    value = _PAGE_RE.sub("", value)
    return value.replace("\\\\", "\\")


class StepAssembly:
    def __init__(
        self,
        name: str,
        id: str,
        parent: "StepAssembly | None" = None,
        description: str = "",
        product_ref: str | None = None,
        product_id_field: str | None = None,
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
        # STEP entity id of the PRODUCT this instance was created from
        # (several distinct products may share the same display name).
        self.product_ref = product_ref
        # The PRODUCT id field, which often disambiguates same-named products.
        self.product_id_field = product_id_field or name
        # Index among siblings that share this name, in file order. Together
        # with the ancestor chain this identifies the instance in any other
        # reading of the same file (e.g. the XCAF document used for export).
        self.occurrence_index = 0

    def add_child(self, child: "StepAssembly"):
        child.parent = self
        self.children.append(child)

    def get_path(self) -> str:
        if self.parent:
            return f"{self.parent.get_path()}/{self.name}"
        return self.name

    def name_path(self) -> NamePath:
        """Path from root as (name, occurrence-among-same-name-siblings)."""
        path = [(self.name, self.occurrence_index)]
        node = self.parent
        while node is not None:
            path.append((node.name, node.occurrence_index))
            node = node.parent
        path.reverse()
        return tuple(path)

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

    return (new_x, new_y, new_z), matrix_to_rpy(n11, n21, n31, n32, n33, n22, n23)


class StepParser:
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.assemblies: dict[str, StepAssembly] = {}
        self.root_assemblies: list[StepAssembly] = []
        self.unit_scale = 1.0
        self.unit_name = "UNKNOWN"
        self._nauo_transforms: dict[str, tuple] = {}
        self._product_transforms: dict[str, tuple] = {}

    def parse(self) -> list[StepAssembly]:
        try:
            content = self.filepath.read_text(encoding="utf-8", errors="ignore")

            data_match = re.search(r"DATA;(.*?)ENDSEC;", content, re.DOTALL)
            if not data_match:
                raise ValueError("Could not find DATA section in STEP file")

            data_section = data_match.group(1)
            entities = self._parse_entities(data_section)
            self._extract_units(entities)
            self._extract_transformations(entities)
            self._extract_assemblies(entities)

            return self.root_assemblies

        except Exception as e:
            raise ValueError(f"Failed to parse STEP file: {e}") from e

    def get_unit_info(self) -> tuple[str, float]:
        return (self.unit_name, self.unit_scale)

    def get_assembly(self, assembly_id: str) -> StepAssembly | None:
        """Look up any parsed instance node by its unique instance id."""
        return self.assemblies.get(assembly_id)

    def _parse_entities(self, data_section: str) -> dict[str, str]:
        entities = {}
        # Quoted strings may contain ';' and escape apostrophes by doubling
        # (''), so consume them as a unit instead of stopping at any ';'.
        # The unrolled-loop form scans unquoted runs in one pass instead of
        # one character per regex-engine iteration (~10x faster on big files).
        pattern = r"(#\d+)\s*=\s*([^';]*(?:'[^']*'[^';]*)*);"

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
        # PRODUCT entity id -> (id field, name, description)
        products: dict[str, tuple[str, str, str]] = {}
        product_definitions: dict[str, str] = {}
        product_definition_formations: dict[str, str] = {}

        for entity_id, entity_data in entities.items():
            if entity_data.startswith("PRODUCT("):
                quoted_strings = [
                    _decode_step_string(value)
                    for value in re.findall(r"'((?:[^']|'')*)'", entity_data)
                ]
                if len(quoted_strings) >= 2:
                    id_field = quoted_strings[0]
                    name = quoted_strings[1]
                    description = quoted_strings[2] if len(quoted_strings) >= 3 else ""
                    products[entity_id] = (id_field, name, description)
                elif len(quoted_strings) >= 1:
                    products[entity_id] = (
                        quoted_strings[0],
                        quoted_strings[0],
                        "",
                    )

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
            return

        # product entity id -> [(nauo id, child product entity id)] in file order
        product_children: dict[str, list[tuple[str, str]]] = {}
        child_products: set[str] = set()

        for nauo_id, entity_data in entities.items():
            if "NEXT_ASSEMBLY_USAGE_OCCURRENCE" in entity_data:
                cleaned = re.sub(r"'[^']*'", "''", entity_data)
                refs = re.findall(r"#\d+", cleaned)
                if len(refs) < 2:
                    continue

                parent_ref = prod_def_to_product.get(refs[0])
                child_ref = prod_def_to_product.get(refs[1])
                if not parent_ref or not child_ref or child_ref not in products:
                    continue

                product_children.setdefault(parent_ref, []).append((nauo_id, child_ref))
                child_products.add(child_ref)

        # A product used as a child anywhere is not a root; every occurrence of
        # it in the tree comes from expanding its parents.
        root_products = [ref for ref in products if ref not in child_products]

        def make_node(
            product_ref: str, node_id: str, nauo_id: str | None
        ) -> StepAssembly:
            id_field, name, description = products[product_ref]
            clean_name = name if name else f"Part_{product_ref}"
            node = StepAssembly(
                clean_name,
                node_id,
                description=description,
                product_ref=product_ref,
                product_id_field=id_field,
            )
            node.is_origin = any(
                keyword in clean_name.lower() for keyword in ORIGIN_KEYWORDS
            )
            transform = None
            if nauo_id is not None:
                transform = self._nauo_transforms.get(nauo_id)
            if transform is None:
                transform = self._product_transforms.get(product_ref)
            if transform is not None:
                node.position, node.rotation = transform
            self.assemblies[node_id] = node
            return node

        # Expand the product graph into a tree with one node per instance
        # path, so each occurrence of a reused sub-assembly gets its own
        # parent chain and transform.
        def expand(node: StepAssembly, product_ref: str, active: set[str]):
            active.add(product_ref)
            for nauo_id, child_ref in product_children.get(product_ref, []):
                if child_ref in active:
                    # Cycle in the assembly graph (corrupt file); skip.
                    continue
                child_id = f"{node.id}/{nauo_id}"
                child = make_node(child_ref, child_id, nauo_id)
                node.add_child(child)
                expand(child, child_ref, active)
            active.discard(product_ref)
            assign_occurrence_indices(node.children)

        for product_ref in root_products:
            root = make_node(product_ref, product_ref, None)
            self.root_assemblies.append(root)
            expand(root, product_ref, set())

        assign_occurrence_indices(self.root_assemblies)

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

            nauo_data = entities.get(nauo_ref, "")
            if "NEXT_ASSEMBLY_USAGE_OCCURRENCE" in nauo_data:
                self._nauo_transforms[nauo_ref] = (position, rotation)
            else:
                # The transform points at something other than a NAUO; keep it
                # keyed by the product it defines so root nodes can use it.
                cleaned = re.sub(r"'[^']*'", "''", nauo_data)
                refs = re.findall(r"#\d+", cleaned)
                if len(refs) >= 2:
                    product_ref = self._product_for_product_def(refs[1], entities)
                    if product_ref is not None:
                        self._product_transforms.setdefault(
                            product_ref, (position, rotation)
                        )

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

        # The rotation matrix's columns are the x/y/z axes.
        return matrix_to_rpy(x[0], x[1], x[2], y[2], z[2], y[1], z[1])

    def _normalize_vector(
        self, v: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        mag = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        if mag < 1e-10:
            return (1.0, 0.0, 0.0)
        return (v[0] / mag, v[1] / mag, v[2] / mag)

    def _product_for_product_def(
        self, prod_def_ref: str, entities: dict[str, str]
    ) -> str | None:
        if prod_def_ref not in entities:
            return None

        prod_def_data = entities[prod_def_ref]
        if "PRODUCT_DEFINITION" not in prod_def_data:
            return None

        refs = re.findall(r"#\d+", prod_def_data)
        if not refs:
            return None

        formation_ref = refs[0]
        if formation_ref not in entities:
            return None

        formation_data = entities[formation_ref]
        if "PRODUCT_DEFINITION_FORMATION" not in formation_data:
            return None

        product_refs = re.findall(r"#\d+", formation_data)
        if not product_refs:
            return None

        return product_refs[0]
