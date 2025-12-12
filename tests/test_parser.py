"""Tests for STEP parser."""

from pathlib import Path

import pytest

from step_to_scene.parser import StepParser


@pytest.fixture
def sample_step_file():
    """Path to sample STEP file."""
    return Path(__file__).parent / "sample.step"


def test_parser_can_read_step_file(sample_step_file):
    """Test that parser can read a STEP file."""
    parser = StepParser(sample_step_file)
    assemblies = parser.parse()

    assert assemblies is not None
    assert isinstance(assemblies, list)


def test_parser_extracts_assemblies(sample_step_file):
    """Test that parser extracts assemblies."""
    parser = StepParser(sample_step_file)
    assemblies = parser.parse()

    assert len(assemblies) > 0


def test_parser_invalid_file():
    """Test that parser handles invalid files."""
    parser = StepParser(Path("nonexistent.step"))

    with pytest.raises(Exception):
        parser.parse()


def test_assembly_structure(sample_step_file):
    """Test that assemblies have proper structure."""
    parser = StepParser(sample_step_file)
    assemblies = parser.parse()

    # Check that at least one assembly was found
    assert len(assemblies) > 0

    # Check that assembly has required attributes
    assembly = assemblies[0]
    assert hasattr(assembly, "name")
    assert hasattr(assembly, "id")
    assert hasattr(assembly, "children")

    # Name should not be empty
    assert assembly.name
