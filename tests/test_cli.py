"""Tests for CLI."""

from pathlib import Path
from click.testing import CliRunner
import pytest

from step_to_scene.cli import main


@pytest.fixture
def sample_step_file():
    """Path to sample STEP file."""
    return Path(__file__).parent / "sample.step"


@pytest.fixture
def runner():
    """CLI test runner."""
    return CliRunner()


def test_cli_version(runner):
    """Test version command."""
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "version" in result.output.lower() or "0.1.0" in result.output


def test_cli_help(runner):
    """Test help command."""
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_list_assemblies(runner, sample_step_file):
    """Test list-assemblies command."""
    result = runner.invoke(main, ["list-assemblies", str(sample_step_file)])
    assert result.exit_code == 0
    assert "assemblies" in result.output.lower()


def test_export_command(runner, sample_step_file, tmp_path):
    """Test export command."""
    output_file = tmp_path / "output.urdf"
    result = runner.invoke(
        main, ["export", str(sample_step_file), "-f", "urdf", "-o", str(output_file)]
    )
    assert result.exit_code == 0
    assert output_file.exists()


def test_export_default_format(runner, sample_step_file, tmp_path):
    """Test export command with default format."""
    # Use tmp_path as working directory
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # Copy the step file to the isolated filesystem
        import shutil

        local_step = Path("sample.step")
        shutil.copy(sample_step_file, local_step)

        result = runner.invoke(main, ["export", str(local_step)])
        assert result.exit_code == 0

        # Check that default output file was created
        expected_output = Path("sample_converted.urdf")
        assert expected_output.exists()


def test_export_invalid_format(runner, sample_step_file):
    """Test export command with invalid format."""
    result = runner.invoke(main, ["export", str(sample_step_file), "-f", "invalid"])
    assert result.exit_code != 0
