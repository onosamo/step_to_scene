from pathlib import Path

import pytest

from step_to_scene.visualizer import create_transform_matrix
from step_to_scene.xml_utils import parse_urdf_mesh_info, parse_xacro_with_transforms


@pytest.fixture
def test_dir() -> Path:
    return Path(__file__).parent.parent / "test_run"


@pytest.mark.skipif(
    not (
        Path(__file__).parent.parent / "test_run" / "correct_export_test.xacro"
    ).exists(),
    reason="correct_export_test.xacro not found",
)
def test_visualization_pipeline(test_dir: Path):
    xacro_file = test_dir / "correct_export_test.xacro"

    included_urdfs, transforms = parse_xacro_with_transforms(xacro_file)

    assert len(included_urdfs) > 0, "Should have at least one included URDF"
    assert len(transforms) > 0, "Should have at least one transform"

    for urdf_path in included_urdfs:
        mesh_file, link_name, scale = parse_urdf_mesh_info(urdf_path)

        assert link_name is not None, "Link name should not be None"
        assert mesh_file is not None, "Mesh file should not be None"
        assert scale is not None, "Scale should not be None"

        mesh_path = urdf_path.parent / mesh_file
        assert mesh_path.exists(), f"Mesh file should exist: {mesh_path}"

        if link_name in transforms:
            transform_data = transforms[link_name]
            xyz = transform_data["xyz"]
            rpy = transform_data["rpy"]
            T = create_transform_matrix(xyz, rpy)
            assert T.shape == (4, 4), "Transform should be 4x4 matrix"
