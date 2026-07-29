"""Geometry access for STEP files via OCCT's XCAF document.

The regex-based :mod:`step_to_scene.parser` builds the assembly tree shown in
the UI; this module reads the same file through XCAF to get exact shapes and
placements. The two readings are joined by the path of
(product name, occurrence index) pairs from the root, which both sides derive
from the same NEXT_ASSEMBLY_USAGE_OCCURRENCE order — never by name alone,
since distinct products may share a name.
"""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from step_to_scene.parser import (
    NamePath,
    assign_occurrence_indices,
    matrix_to_rpy,
    normalize_step_name,
)

__all__ = [
    "GeometryInstance",
    "NamePath",
    "StepGeometry",
    "transform_to_xyz_rpy",
]

_CACHE_FORMAT_VERSION = 1
_session_cache: dict[Path, "StepGeometry"] = {}
_session_cache_lock = threading.Lock()


@dataclass
class GeometryInstance:
    name: str
    occurrence_index: int
    product_key: str
    """Unique key of the product this instance refers to (OCAF label entry)."""
    absolute_transform: object
    """gp_Trsf from the file root to this instance, in file units."""
    local_transform: object
    """gp_Trsf relative to the parent instance, in file units."""
    children: list["GeometryInstance"] = field(default_factory=list)
    _child_index: dict[tuple[str, int], "GeometryInstance"] | None = field(
        default=None, repr=False, compare=False
    )

    def find_child(self, name: str, occurrence_index: int) -> "GeometryInstance | None":
        if self._child_index is None:
            self._child_index = {
                (child.name, child.occurrence_index): child for child in self.children
            }
        return self._child_index.get((name, occurrence_index))


class StepGeometry:
    """Shapes and exact placements of every assembly instance in a STEP file.

    Loading a big STEP file through OCCT takes minutes, so loads are cached at
    two levels: :meth:`for_file` keeps the loaded geometry for the rest of the
    session, and a disk cache next to the STEP file (``<name>.stsc.json`` +
    ``<name>.stsc.brep``) makes the next process start fast. Both are safe to
    delete; they are rebuilt when the STEP file changes.
    """

    def __init__(self, step_file: Path, use_disk_cache: bool = True):
        self.step_file = step_file
        self.use_disk_cache = use_disk_cache
        self.roots: list[GeometryInstance] = []
        self.loaded_from_cache = False
        self._shapes: dict[str, object] = {}
        self._loaded = False
        self._load_lock = threading.Lock()

    @classmethod
    def for_file(cls, step_file: Path, use_disk_cache: bool = True) -> "StepGeometry":
        """Shared per-session instance, so repeated exports load only once."""
        key = Path(step_file).resolve()
        with _session_cache_lock:
            geometry = _session_cache.get(key)
            if geometry is None:
                geometry = cls(step_file, use_disk_cache=use_disk_cache)
                _session_cache[key] = geometry
            return geometry

    def _cache_paths(self) -> tuple[Path, Path]:
        base = self.step_file.parent / f"{self.step_file.name}.stsc"
        return base.with_suffix(".stsc.json"), base.with_suffix(".stsc.brep")

    def load(self, progress_callback: Callable[[str], None] | None = None) -> None:
        with self._load_lock:
            if self._loaded:
                return
            self._load(progress_callback)

    def _load(self, progress_callback: Callable[[str], None] | None) -> None:
        def report(msg: str):
            print(msg)
            if progress_callback:
                progress_callback(msg)

        if self.use_disk_cache and self._load_from_disk_cache(report):
            self._loaded = True
            self.loaded_from_cache = True
            return

        self._load_from_step(report)
        self._loaded = True
        if self.use_disk_cache:
            self._save_disk_cache(report)

    def _load_from_step(self, report: Callable[[str], None]) -> None:
        from OCP.gp import gp_Trsf
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
        from OCP.TDataStd import TDataStd_Name
        from OCP.TDF import TDF_Label, TDF_LabelSequence, TDF_Tool
        from OCP.TDocStd import TDocStd_Document
        from OCP.TopoDS import TopoDS_Shape
        from OCP.XCAFDoc import XCAFDoc_DocumentTool

        report(
            f"Loading CAD geometry from {self.step_file.name} (this can take a while)..."
        )

        doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
        reader = STEPCAFControl_Reader()
        reader.SetNameMode(True)

        status = reader.ReadFile(str(self.step_file))
        if status != 1:
            raise ValueError(f"Failed to read STEP file: {self.step_file}")

        report("Transferring CAD document...")
        reader.Transfer(doc)
        shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

        def label_entry(label: TDF_Label) -> str:
            entry = TCollection_AsciiString()
            TDF_Tool.Entry_s(label, entry)
            return entry.ToCString()

        def label_name(label: TDF_Label) -> str | None:
            attr = TDataStd_Name()
            if label.FindAttribute(attr.GetID_s(), attr):
                return normalize_step_name(attr.Get().ToExtString())
            return None

        def store_shape(label: TDF_Label) -> str:
            key = label_entry(label)
            if key not in self._shapes:
                shape = TopoDS_Shape()
                if shape_tool.GetShape_s(label, shape) and not shape.IsNull():
                    self._shapes[key] = shape
            return key

        def build(
            product_label: TDF_Label,
            name: str,
            parent_transform: gp_Trsf,
            local_transform: gp_Trsf,
        ) -> GeometryInstance:
            absolute = parent_transform.Multiplied(local_transform)
            node = GeometryInstance(
                name=name,
                occurrence_index=0,
                product_key=store_shape(product_label),
                absolute_transform=absolute,
                local_transform=local_transform,
            )

            components = TDF_LabelSequence()
            if shape_tool.GetComponents_s(product_label, components, False):
                for i in range(1, components.Length() + 1):
                    component = components.Value(i)
                    referred = TDF_Label()
                    if shape_tool.GetReferredShape_s(component, referred):
                        target = referred
                    else:
                        target = component
                    child_name = (
                        label_name(target) or label_name(component) or "unnamed"
                    )
                    location = shape_tool.GetLocation_s(component)
                    node.children.append(
                        build(target, child_name, absolute, location.Transformation())
                    )

            assign_occurrence_indices(node.children)
            return node

        free_labels = TDF_LabelSequence()
        shape_tool.GetFreeShapes(free_labels)
        report(f"Building instance tree ({free_labels.Length()} root shapes)...")

        identity = gp_Trsf()
        for i in range(1, free_labels.Length() + 1):
            label = free_labels.Value(i)
            name = label_name(label) or "unnamed"
            location = shape_tool.GetLocation_s(label)
            self.roots.append(build(label, name, identity, location.Transformation()))

        assign_occurrence_indices(self.roots)
        report(
            f"CAD geometry ready: {len(self._shapes)} products, "
            f"{_count_instances(self.roots)} instances"
        )

    def _source_signature(self) -> dict:
        stat = self.step_file.stat()
        return {"source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns}

    def _save_disk_cache(self, report: Callable[[str], None]) -> None:
        from OCP.BinTools import BinTools
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound

        json_path, brep_path = self._cache_paths()
        try:
            start_time = time.time()
            product_keys = list(self._shapes)
            builder = BRep_Builder()
            compound = TopoDS_Compound()
            builder.MakeCompound(compound)
            for key in product_keys:
                builder.Add(compound, self._shapes[key])

            # The brep is written first: an interrupted save leaves no json,
            # so a half-written cache is never picked up.
            brep_tmp = brep_path.with_suffix(".brep.tmp")
            BinTools.Write_s(compound, str(brep_tmp))
            brep_tmp.replace(brep_path)

            payload = {
                "version": _CACHE_FORMAT_VERSION,
                **self._source_signature(),
                "product_keys": product_keys,
                "roots": [_serialize_instance(root) for root in self.roots],
            }
            json_tmp = json_path.with_suffix(".json.tmp")
            json_tmp.write_text(json.dumps(payload))
            json_tmp.replace(json_path)

            size_mb = brep_path.stat().st_size / (1024 * 1024)
            report(
                f"Saved geometry cache ({size_mb:.0f}MB in "
                f"{time.time() - start_time:.0f}s): {brep_path.name}"
            )
        except Exception as e:
            report(f"Could not save geometry cache (continuing without): {e}")
            for path in (json_path, brep_path):
                path.unlink(missing_ok=True)

    def _load_from_disk_cache(self, report: Callable[[str], None]) -> bool:
        from OCP.BinTools import BinTools
        from OCP.TopoDS import TopoDS_Iterator, TopoDS_Shape

        json_path, brep_path = self._cache_paths()
        if not json_path.exists() or not brep_path.exists():
            return False

        try:
            payload = json.loads(json_path.read_text())
            if payload.get("version") != _CACHE_FORMAT_VERSION:
                return False
            signature = self._source_signature()
            if (
                payload.get("source_size") != signature["source_size"]
                or payload.get("source_mtime_ns") != signature["source_mtime_ns"]
            ):
                report("Geometry cache is stale (STEP file changed), rebuilding...")
                return False

            start_time = time.time()
            report(f"Loading geometry cache {brep_path.name}...")

            compound = TopoDS_Shape()
            BinTools.Read_s(compound, str(brep_path))
            product_keys = payload["product_keys"]

            shapes: dict[str, object] = {}
            iterator = TopoDS_Iterator(compound)
            for key in product_keys:
                if not iterator.More():
                    raise ValueError("cache shape count mismatch")
                shapes[key] = iterator.Value()
                iterator.Next()

            from OCP.gp import gp_Trsf

            identity = gp_Trsf()
            self.roots = [
                _deserialize_instance(root, identity) for root in payload["roots"]
            ]
            self._shapes = shapes
            report(
                f"CAD geometry ready from cache in {time.time() - start_time:.0f}s: "
                f"{len(shapes)} products, {_count_instances(self.roots)} instances"
            )
            return True
        except Exception as e:
            report(f"Geometry cache unusable ({e}), reading STEP file instead...")
            self.roots = []
            self._shapes = {}
            return False

    def find(self, name_path: NamePath) -> GeometryInstance | None:
        if not name_path:
            return None
        name, occurrence = name_path[0]
        node = None
        for root in self.roots:
            if root.name == name and root.occurrence_index == occurrence:
                node = root
                break
        for name, occurrence in name_path[1:]:
            if node is None:
                return None
            node = node.find_child(name, occurrence)
        return node

    def shape_for(self, instance: GeometryInstance):
        """The instance's product shape, in product-local coordinates."""
        return self._shapes.get(instance.product_key)

    def shape_excluding(
        self, instance: GeometryInstance, excluded_paths: set[NamePath]
    ):
        """The instance's shape minus excluded descendants, local coordinates.

        ``excluded_paths`` are name paths relative to ``instance``.
        """
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound

        if not excluded_paths:
            return self.shape_for(instance)

        # Every proper prefix of an excluded path marks a subtree that must be
        # rebuilt rather than used whole.
        excluded_prefixes = {
            path[:length] for path in excluded_paths for length in range(1, len(path))
        }

        def assemble(node: GeometryInstance, prefix: NamePath):
            builder = BRep_Builder()
            compound = TopoDS_Compound()
            builder.MakeCompound(compound)
            added = False
            for child in node.children:
                child_path = prefix + ((child.name, child.occurrence_index),)
                if child_path in excluded_paths:
                    continue
                if child_path in excluded_prefixes and child.children:
                    child_shape = assemble(child, child_path)
                else:
                    child_shape = self.shape_for(child)
                if child_shape is None or child_shape.IsNull():
                    continue
                builder.Add(
                    compound, child_shape.Moved(_to_location(child.local_transform))
                )
                added = True
            return compound if added else None

        if not instance.children:
            return self.shape_for(instance)
        return assemble(instance, ())

    @staticmethod
    def write_stl(
        shape,
        output_path: Path,
        linear_deflection: float = 1.0,
        angular_deflection: float = 0.5,
    ) -> tuple[bool, str]:
        """Mesh ``shape`` and write a binary STL. Returns (ok, reason)."""
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.StlAPI import StlAPI_Writer
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer

        if shape is None or shape.IsNull():
            return False, "no geometry"

        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        if not explorer.More():
            return False, "shape has no faces to mesh"

        mesh = BRepMesh_IncrementalMesh(
            shape, linear_deflection, False, angular_deflection, True
        )
        mesh.Perform()
        if not mesh.IsDone():
            return False, "meshing failed"

        writer = StlAPI_Writer()
        writer.ASCIIMode = False
        if not writer.Write(shape, str(output_path)):
            return False, "STL write failed"
        return True, ""


def _trsf_to_list(transform) -> list[float]:
    return [
        transform.Value(row, column) for row in range(1, 4) for column in range(1, 5)
    ]


def _trsf_from_list(values: list[float]):
    from OCP.gp import gp_Trsf

    transform = gp_Trsf()
    transform.SetValues(*values)
    return transform


def _serialize_instance(node: GeometryInstance) -> dict:
    return {
        "name": node.name,
        "occ": node.occurrence_index,
        "key": node.product_key,
        "local": _trsf_to_list(node.local_transform),
        "children": [_serialize_instance(child) for child in node.children],
    }


def _deserialize_instance(data: dict, parent_transform) -> GeometryInstance:
    local = _trsf_from_list(data["local"])
    absolute = parent_transform.Multiplied(local)
    return GeometryInstance(
        name=data["name"],
        occurrence_index=data["occ"],
        product_key=data["key"],
        absolute_transform=absolute,
        local_transform=local,
        children=[_deserialize_instance(child, absolute) for child in data["children"]],
    )


def _count_instances(roots: list[GeometryInstance]) -> int:
    total = 0
    stack = list(roots)
    while stack:
        node = stack.pop()
        total += 1
        stack.extend(node.children)
    return total


def _to_location(transform):
    from OCP.TopLoc import TopLoc_Location

    return TopLoc_Location(transform)


def transform_to_xyz_rpy(
    transform,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """gp_Trsf -> translation and URDF fixed-axis roll/pitch/yaw (radians)."""
    translation = transform.TranslationPart()
    xyz = (translation.X(), translation.Y(), translation.Z())

    rpy = matrix_to_rpy(
        transform.Value(1, 1),
        transform.Value(2, 1),
        transform.Value(3, 1),
        transform.Value(3, 2),
        transform.Value(3, 3),
        transform.Value(2, 2),
        transform.Value(2, 3),
    )

    return xyz, rpy
