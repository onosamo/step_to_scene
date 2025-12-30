from pathlib import Path

import pytest


@pytest.fixture
def test_dir() -> Path:
    return Path(__file__).parent.parent / "test_run"


@pytest.fixture
def test_step_file() -> Path:
    return Path(__file__).parent.parent / "test_step.step"
