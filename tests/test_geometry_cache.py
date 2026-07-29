"""Tests for the geometry disk/session cache."""

import os
import shutil
from pathlib import Path

import pytest

from step_to_scene.geometry import StepGeometry, transform_to_xyz_rpy

PIN_PATH = (("cell", 0), ("subasm", 1), ("pin", 0))


@pytest.fixture
def step_copy(assembly_step_file: Path, tmp_path: Path) -> Path:
    step = tmp_path / "assembly.step"
    shutil.copy(assembly_step_file, step)
    return step


def _flatten(nodes, prefix=()):
    result = []
    for node in nodes:
        path = prefix + ((node.name, node.occurrence_index),)
        result.append((path, node.product_key))
        result.extend(_flatten(node.children, path))
    return result


def test_first_load_writes_cache(step_copy: Path):
    geometry = StepGeometry(step_copy)
    geometry.load()

    assert not geometry.loaded_from_cache
    assert (step_copy.parent / "assembly.step.stsc.json").exists()
    assert (step_copy.parent / "assembly.step.stsc.brep").exists()


def test_cache_round_trip_preserves_tree_and_shapes(step_copy: Path, tmp_path: Path):
    first = StepGeometry(step_copy)
    first.load()

    second = StepGeometry(step_copy)
    second.load()

    assert second.loaded_from_cache
    assert _flatten(first.roots) == _flatten(second.roots)

    instance = second.find(PIN_PATH)
    assert instance is not None
    xyz, _ = transform_to_xyz_rpy(instance.absolute_transform)
    assert xyz[2] == pytest.approx(203.0, abs=1e-6)

    ok, reason = StepGeometry.write_stl(
        second.shape_for(instance), tmp_path / "pin.stl"
    )
    assert ok, reason


def test_cache_stale_when_source_changes(step_copy: Path):
    StepGeometry(step_copy).load()

    stat = step_copy.stat()
    os.utime(step_copy, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    reloaded = StepGeometry(step_copy)
    reloaded.load()
    assert not reloaded.loaded_from_cache


def test_corrupt_cache_falls_back_to_step(step_copy: Path):
    StepGeometry(step_copy).load()
    (step_copy.parent / "assembly.step.stsc.json").write_text("{not json")

    reloaded = StepGeometry(step_copy)
    reloaded.load()
    assert not reloaded.loaded_from_cache
    assert reloaded.find(PIN_PATH) is not None


def test_disk_cache_can_be_disabled(step_copy: Path):
    geometry = StepGeometry(step_copy, use_disk_cache=False)
    geometry.load()

    assert not (step_copy.parent / "assembly.step.stsc.json").exists()
    assert not (step_copy.parent / "assembly.step.stsc.brep").exists()


def test_for_file_returns_shared_instance(step_copy: Path):
    first = StepGeometry.for_file(step_copy)
    second = StepGeometry.for_file(step_copy)
    assert first is second
