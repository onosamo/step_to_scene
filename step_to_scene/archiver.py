import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

from step_to_scene.xml_utils import (
    find_xacro_includes,
    parse_xml_with_comments,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def collect_urdf_dependencies(
    urdf_path: Path, root_dir: Path | None = None
) -> set[Path]:
    if root_dir is None:
        root_dir = urdf_path.parent

    dependencies: set[Path] = {urdf_path}

    tree = parse_xml_with_comments(urdf_path)
    root = tree.getroot()

    for include in find_xacro_includes(root):
        filename = include.get("filename")
        if filename:
            include_path = urdf_path.parent / filename
            if include_path.exists() and include_path not in dependencies:
                dependencies.add(include_path)
                dependencies.update(collect_urdf_dependencies(include_path, root_dir))

    for mesh in root.findall(".//mesh"):
        filename = mesh.get("filename")
        if filename:
            clean_filename = filename.replace("package://", "")
            mesh_path = urdf_path.parent / clean_filename
            if mesh_path.exists():
                dependencies.add(mesh_path)

    return dependencies


def create_archive(
    main_file: Path,
    output_archive: Path,
    include_step: bool = True,
    progress_callback: "Callable[[str], None] | None" = None,
) -> None:
    if not main_file.exists():
        raise FileNotFoundError(f"Main file not found: {main_file}")

    if progress_callback:
        progress_callback(f"Collecting dependencies for: {main_file}")

    dependencies = collect_urdf_dependencies(main_file)

    if progress_callback:
        mesh_count = sum(1 for d in dependencies if d.suffix.lower() == ".stl")
        urdf_count = sum(
            1 for d in dependencies if d.suffix.lower() in [".urdf", ".xacro"]
        )
        progress_callback(
            f"Found {len(dependencies)} dependencies ({urdf_count} URDF/xacro, {mesh_count} meshes)"
        )

    if include_step:
        base_name = main_file.stem.replace("_converted", "").replace("_simplified", "")
        for ext in [".step", ".stp", ".STEP", ".STP"]:
            step_file = main_file.parent / f"{base_name}{ext}"
            if step_file.exists():
                dependencies.add(step_file)
                if progress_callback:
                    progress_callback(f"Found STEP file: {step_file.name}")
                break

    root_dir = main_file.parent.resolve()

    if progress_callback:
        progress_callback(f"Creating archive: {output_archive}")

    with tarfile.open(output_archive, "w:gz") as tar:
        for file_path in sorted(dependencies):
            try:
                file_path = file_path.resolve()
                arcname = file_path.relative_to(root_dir)
                tar.add(file_path, arcname=arcname)
                if progress_callback:
                    progress_callback(f"  Added: {arcname}")
            except ValueError:
                if progress_callback:
                    progress_callback(f"  Skipped (outside root): {file_path}")

    if progress_callback:
        file_size = output_archive.stat().st_size / (1024 * 1024)
        progress_callback(f"Archive created: {output_archive} ({file_size:.2f} MB)")


def archive_assembly(
    main_file: Path,
    output_dir: Path | None = None,
    include_step: bool = True,
    create_simplified: bool = True,
    progress_callback: "Callable[[str], None] | None" = None,
) -> tuple[Path, Path | None]:
    if output_dir is None:
        output_dir = main_file.parent

    output_dir.mkdir(parents=True, exist_ok=True)

    original_archive = output_dir / f"{main_file.stem}_archive.tar.gz"
    if progress_callback:
        progress_callback("\n=== Creating Original Archive ===")
    create_archive(main_file, original_archive, include_step, progress_callback)

    simplified_archive = None
    if create_simplified:
        simplified_file = main_file.with_name(
            f"{main_file.stem}_simplified{main_file.suffix}"
        )
        if simplified_file.exists():
            if progress_callback:
                progress_callback("\n=== Creating Simplified Archive ===")
            simplified_archive = output_dir / f"{simplified_file.stem}_archive.tar.gz"
            create_archive(
                simplified_file, simplified_archive, include_step, progress_callback
            )
        elif progress_callback:
            progress_callback(f"\nNo simplified version found at: {simplified_file}")
            progress_callback("Skipping simplified archive creation.")

    return original_archive, simplified_archive
