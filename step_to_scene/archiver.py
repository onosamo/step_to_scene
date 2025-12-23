"""Archive utility for packaging URDF/XACRO/STEP files with dependencies."""

import re
import tarfile
from pathlib import Path
from xml.etree import ElementTree as ET


class CommentedTreeBuilder(ET.TreeBuilder):
    """TreeBuilder that preserves comments."""

    def comment(self, data):
        """Handle comments."""
        self.start(ET.Comment, {})
        self.data(data)
        self.end(ET.Comment)


def collect_urdf_dependencies(
    urdf_path: Path, root_dir: Path | None = None
) -> set[Path]:
    """Collect all file dependencies from a URDF/XACRO file.

    Args:
        urdf_path: Path to the URDF/XACRO file
        root_dir: Root directory to resolve paths relative to (default: urdf parent)

    Returns:
        Set of all file paths referenced in the URDF
    """
    if root_dir is None:
        root_dir = urdf_path.parent

    dependencies = {urdf_path}

    try:
        parser = ET.XMLParser(target=CommentedTreeBuilder())
        tree = ET.parse(urdf_path, parser)
    except ET.ParseError:
        # Try without comments if parsing fails
        with open(urdf_path, encoding="utf-8") as f:
            content = f.read()
        content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
        root = ET.fromstring(content)
        tree = ET.ElementTree(root)

    root = tree.getroot()

    # Find xacro includes
    ns = {"xacro": "http://www.ros.org/wiki/xacro"}
    for include in root.findall(".//xacro:include", ns):
        filename = include.get("filename")
        if filename:
            include_path = urdf_path.parent / filename
            if include_path.exists() and include_path not in dependencies:
                dependencies.add(include_path)
                # Recursively collect dependencies from included file
                dependencies.update(collect_urdf_dependencies(include_path, root_dir))

    # Find regular includes
    for include in root.findall(".//include"):
        filename = include.get("filename")
        if filename:
            include_path = urdf_path.parent / filename
            if include_path.exists() and include_path not in dependencies:
                dependencies.add(include_path)
                dependencies.update(collect_urdf_dependencies(include_path, root_dir))

    # Find mesh files
    for mesh in root.findall(".//mesh"):
        filename = mesh.get("filename")
        if filename:
            # Handle package:// URLs and relative paths
            clean_filename = filename.replace("package://", "")
            mesh_path = urdf_path.parent / clean_filename
            if mesh_path.exists():
                dependencies.add(mesh_path)

    return dependencies


def create_archive(
    main_file: Path,
    output_archive: Path,
    include_step: bool = True,
    progress_callback=None,
) -> None:
    """Create a tarball archive with all dependencies.

    Args:
        main_file: Main URDF/XACRO file
        output_archive: Output archive path (.tar.gz)
        include_step: Whether to include STEP files
        progress_callback: Optional callback function(message: str)
    """
    if not main_file.exists():
        raise FileNotFoundError(f"Main file not found: {main_file}")

    if progress_callback:
        progress_callback(f"Collecting dependencies for: {main_file}")

    # Collect all dependencies
    dependencies = collect_urdf_dependencies(main_file)

    if progress_callback:
        progress_callback(f"Found {len(dependencies)} file dependencies")

    # Find associated meshes directory
    mesh_dir = main_file.parent / f"{main_file.stem}_meshes"
    if mesh_dir.exists() and mesh_dir.is_dir():
        mesh_files = list(mesh_dir.glob("**/*"))
        mesh_files = [f for f in mesh_files if f.is_file()]
        dependencies.update(mesh_files)
        if progress_callback:
            progress_callback(f"Found mesh directory with {len(mesh_files)} files")

    # Find parts directory (for xacro assemblies)
    parts_dir = main_file.parent / f"{main_file.stem}_parts"
    if parts_dir.exists() and parts_dir.is_dir():
        part_files = list(parts_dir.glob("**/*"))
        part_files = [f for f in part_files if f.is_file()]
        dependencies.update(part_files)
        if progress_callback:
            progress_callback(f"Found parts directory with {len(part_files)} files")

    # Find STEP file if requested
    step_files = []
    if include_step:
        # Look for STEP file with similar name
        base_name = main_file.stem.replace("_converted", "").replace("_simplified", "")
        for ext in [".step", ".stp", ".STEP", ".STP"]:
            step_file = main_file.parent / f"{base_name}{ext}"
            if step_file.exists():
                step_files.append(step_file)
                dependencies.add(step_file)
                if progress_callback:
                    progress_callback(f"Found STEP file: {step_file.name}")
                break

    # Determine root directory for relative paths
    root_dir = main_file.parent

    # Create archive
    if progress_callback:
        progress_callback(f"Creating archive: {output_archive}")

    with tarfile.open(output_archive, "w:gz") as tar:
        for file_path in sorted(dependencies):
            try:
                # Get relative path for archive
                arcname = file_path.relative_to(root_dir)
                tar.add(file_path, arcname=arcname)
                if progress_callback:
                    progress_callback(f"  Added: {arcname}")
            except ValueError:
                # File is outside root_dir, use absolute path
                if progress_callback:
                    progress_callback(f"  Skipped (outside root): {file_path}")

    if progress_callback:
        file_size = output_archive.stat().st_size / (1024 * 1024)
        progress_callback(f"✓ Archive created: {output_archive} ({file_size:.2f} MB)")


def archive_assembly(
    main_file: Path,
    output_dir: Path | None = None,
    include_step: bool = True,
    create_simplified: bool = True,
    progress_callback=None,
) -> tuple[Path, Path | None]:
    """Create archives for both original and simplified assemblies.

    Args:
        main_file: Main URDF/XACRO file
        output_dir: Output directory for archives (default: same as main_file)
        include_step: Whether to include STEP files in archive
        create_simplified: Whether to create simplified archive
        progress_callback: Optional callback function(message: str)

    Returns:
        Tuple of (original_archive_path, simplified_archive_path or None)
    """
    if output_dir is None:
        output_dir = main_file.parent

    output_dir.mkdir(parents=True, exist_ok=True)

    # Create original archive
    original_archive = output_dir / f"{main_file.stem}_archive.tar.gz"
    if progress_callback:
        progress_callback("\n=== Creating Original Archive ===")
    create_archive(main_file, original_archive, include_step, progress_callback)

    # Create simplified archive if requested and simplified file exists
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
        else:
            if progress_callback:
                progress_callback(
                    f"\nNo simplified version found at: {simplified_file}"
                )
                progress_callback("Skipping simplified archive creation.")

    return original_archive, simplified_archive
