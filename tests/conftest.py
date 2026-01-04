from pathlib import Path

import pytest


@pytest.fixture
def test_data_dir() -> Path:
    """Return the path to the tests/data directory."""
    return Path(__file__).parent / "data"


@pytest.fixture
def test_step_file(test_data_dir: Path) -> Path:
    """Return the path to the test STEP file."""
    return test_data_dir / "test_step.step"
