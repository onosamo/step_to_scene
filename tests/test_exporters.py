"""Tests for exporters."""

from pathlib import Path
import tempfile

import pytest

from step_to_scene.exporters import URDFExporter, XACROExporter, SDFExporter, get_exporter
from step_to_scene.parser import StepAssembly, StepParser


@pytest.fixture
def sample_step_file():
    """Path to sample STEP file."""
    return Path(__file__).parent / "sample.step"


@pytest.fixture
def sample_assemblies(sample_step_file):
    """Parse and return sample assemblies."""
    parser = StepParser(sample_step_file)
    return parser.parse()


def test_get_exporter_urdf():
    """Test getting URDF exporter."""
    exporter = get_exporter("urdf")
    assert isinstance(exporter, URDFExporter)


def test_get_exporter_xacro():
    """Test getting XACRO exporter."""
    exporter = get_exporter("xacro")
    assert isinstance(exporter, XACROExporter)


def test_get_exporter_sdf():
    """Test getting SDF exporter."""
    exporter = get_exporter("sdf")
    assert isinstance(exporter, SDFExporter)


def test_get_exporter_invalid():
    """Test that invalid format raises error."""
    with pytest.raises(ValueError):
        get_exporter("invalid")


def test_urdf_export(sample_assemblies):
    """Test URDF export."""
    exporter = URDFExporter()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".urdf", delete=False) as f:
        output_path = Path(f.name)

    try:
        exporter.export(sample_assemblies, output_path, base_link_name="world", unit_scale=1.0)

        # Check that file was created
        assert output_path.exists()

        # Check that file contains URDF content
        content = output_path.read_text()
        assert "<?xml" in content
        assert "<robot" in content
        assert "</robot>" in content
        assert "world" in content  # base_link name

    finally:
        output_path.unlink(missing_ok=True)


def test_xacro_export(sample_assemblies):
    """Test XACRO export."""
    exporter = XACROExporter()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".xacro", delete=False) as f:
        output_path = Path(f.name)

    try:
        exporter.export(sample_assemblies, output_path, base_link_name="world", unit_scale=1.0)

        # Check that file was created
        assert output_path.exists()

        # Check that file contains XACRO content
        content = output_path.read_text()
        assert "<?xml" in content
        assert "<robot" in content
        assert "xacro" in content

    finally:
        output_path.unlink(missing_ok=True)


def test_sdf_export(sample_assemblies):
    """Test SDF export."""
    exporter = SDFExporter()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sdf", delete=False) as f:
        output_path = Path(f.name)

    try:
        exporter.export(sample_assemblies, output_path, base_link_name="world", unit_scale=1.0)

        # Check that file was created
        assert output_path.exists()

        # Check that file contains SDF content
        content = output_path.read_text()
        assert "<?xml" in content
        assert "<sdf" in content
        assert "<model" in content

    finally:
        output_path.unlink(missing_ok=True)
