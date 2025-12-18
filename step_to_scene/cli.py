"""Command-line interface for step-to-scene."""

from pathlib import Path

import click

from step_to_scene import __version__
from step_to_scene.exporters import get_exporter, get_potential_base_links
from step_to_scene.parser import StepParser
from step_to_scene.tui import run_explorer


@click.group()
@click.version_option(version=__version__)
def main():
    """CLI tool to extract static collision geometry from STEP files to URDF/XACRO/SDF.

    This tool converts large robotic cells from STEP files into robot description
    formats, focusing on extracting static collision geometry. The exported models
    represent static obstacles/environment that users can later replace with proper
    robot descriptions.

    Units are automatically detected and converted to meters if needed.
    """
    pass


@main.command()
@click.argument("step_file", type=click.Path(exists=True, path_type=Path))
def explore(step_file: Path):
    """Explore STEP file assemblies interactively.

    Opens an interactive TUI (Text User Interface) to browse the assembly
    structure of a STEP file. You can navigate the tree, select assemblies,
    and export them as static collision geometry to URDF, XACRO, or SDF formats.

    The exported files will contain placeholder collision geometry that should
    be replaced with actual mesh files or proper dimensions based on the STEP data.

    Example:
        step-to-scene explore robot_cell.step
    """
    click.echo(f"Loading STEP file: {step_file}")

    try:
        run_explorer(step_file)
    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        raise click.Abort()


@main.command()
@click.argument("step_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-f",
    "--format",
    type=click.Choice(["urdf", "xacro", "sdf"], case_sensitive=False),
    default="urdf",
    help="Output format (default: urdf)",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output file path (default: <input_name>_converted.<format>)",
)
@click.option(
    "-b",
    "--base-link",
    type=str,
    default=None,
    help="Name to use for the base/reference link (default: 'world' or auto-detected origin)",
)
@click.option(
    "--list-origins",
    is_flag=True,
    help="List potential origin/base link candidates and exit",
)
def export(
    step_file: Path, format: str, output: Path, base_link: str, list_origins: bool
):
    """Export STEP file assemblies as static collision geometry.

    This command performs a batch conversion of all assemblies in the STEP file
    to the specified format. All parts are exported as static collision objects
    with placeholder geometry that should be replaced with actual mesh files or
    proper dimensions.

    Units are automatically detected from the STEP file and converted to meters.
    Millimeters (mm) will be converted using a 0.001 scale factor.

    The exported models are intended to represent the static environment/obstacles
    in a robotic cell. Users should replace robot parts with proper kinematic
    descriptions afterwards.

    Examples:
        step-to-scene export robot_cell.step -f urdf
        step-to-scene export robot_cell.step -f xacro -o cell.xacro
        step-to-scene export robot_cell.step --base-link robot_origin
        step-to-scene export robot_cell.step --list-origins
    """
    click.echo(f"Loading STEP file: {step_file}")

    try:
        # Parse STEP file
        parser = StepParser(step_file)
        assemblies = parser.parse()

        if not assemblies:
            click.echo("No assemblies found in STEP file.", err=True)
            raise click.Abort()

        # Get unit information
        unit_name, unit_scale = parser.get_unit_info()
        click.echo(f"Detected units: {unit_name} (scale to meters: {unit_scale})")

        if unit_scale != 1.0:
            click.echo(
                f"[WARNING] Units will be converted to meters (scale factor: {unit_scale})"
            )

        click.echo(f"Found {len(assemblies)} top-level assemblies")

        # List potential origins if requested
        potential_origins = get_potential_base_links(assemblies)
        if list_origins:
            if potential_origins:
                click.echo("\nPotential origin/base_link candidates:")
                for origin in potential_origins:
                    click.echo(f"  - {origin.name} (ID: {origin.id})")
            else:
                click.echo("\nNo origin candidates found. Using default 'world'.")
            return

        # Determine base_link name
        if base_link is None:
            if potential_origins:
                base_link = potential_origins[0].name
                click.echo(f"Auto-detected base_link: '{base_link}'")
            else:
                base_link = "world"
                click.echo(f"Using default base_link: '{base_link}'")
        else:
            click.echo(f"Using specified base_link: '{base_link}'")

        # Determine output path
        if output is None:
            output = step_file.parent / f"{step_file.stem}_converted.{format}"

        # Export
        click.echo(
            f"Exporting static collision geometry to {format.upper()} format: {output}"
        )
        exporter = get_exporter(format)
        exporter.step_file = step_file  # Set step file for mesh export
        exporter.export(
            assemblies, output, base_link_name=base_link, unit_scale=unit_scale
        )

        click.echo(f"✓ Successfully exported to {output}")

        # Check if mesh was generated
        mesh_dir = output.parent / f"{output.stem}_meshes"
        if mesh_dir.exists():
            stl_files = list(mesh_dir.glob("*.stl"))
            if stl_files:
                total_size = sum(f.stat().st_size for f in stl_files) / (
                    1024 * 1024
                )  # MB
                click.echo(f"✓ Exported STL mesh ({total_size:.1f} MB) to {mesh_dir}")

    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        raise click.Abort()


@main.command()
@click.argument("urdf_file", type=click.Path(exists=True, path_type=Path))
def visualize(urdf_file: Path):
    """Visualize exported URDF/XACRO file with 3D viewer.

    Opens a 3D visualization of the URDF/XACRO file with all included meshes
    and transformations applied. Useful for verifying the export result.

    Example:
        step-to-scene visualize robot_cell_converted.xacro
    """
    click.echo(f"Loading URDF file: {urdf_file}")

    try:
        from step_to_scene.visualizer import visualize_urdf

        visualize_urdf(urdf_file)
    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        raise click.Abort()


@main.command()
@click.argument("step_file", type=click.Path(exists=True, path_type=Path))
def list_assemblies(step_file: Path):
    """List all assemblies in a STEP file.

    Displays a hierarchical list of all assemblies and parts found in the
    STEP file without opening the interactive TUI. Also shows unit information
    and potential origin candidates.

    Example:
        step-to-scene list-assemblies robot_cell.step
    """
    click.echo(f"Loading STEP file: {step_file}")

    try:
        # Parse STEP file
        parser = StepParser(step_file)
        assemblies = parser.parse()

        # Get unit information
        unit_name, unit_scale = parser.get_unit_info()
        click.echo(f"Detected units: {unit_name} (scale to meters: {unit_scale})")

        if unit_scale != 1.0:
            click.echo(
                f"[WARNING] Units will be converted to meters (scale factor: {unit_scale})"
            )

        if not assemblies:
            click.echo("\nNo assemblies found in STEP file.")
            return

        click.echo(f"\nFound {len(assemblies)} top-level assemblies:\n")

        # Display assemblies
        for assembly in assemblies:
            _print_assembly_tree(assembly, indent=0)

        # Show potential origin candidates
        potential_origins = get_potential_base_links(assemblies)
        if potential_origins:
            click.echo("\n" + "=" * 50)
            click.echo("Potential origin/base_link candidates:")
            for origin in potential_origins:
                click.echo(f"  [ORIGIN] {origin.name} (ID: {origin.id})")
            click.echo(
                "\nUse --base-link option with export command to specify which to use."
            )

    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        raise click.Abort()


@main.command()
@click.argument("urdf_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--offset",
    type=float,
    default=6.0,
    help="Offset distance for collision mesh simplification (default: 6.0)",
)
@click.option(
    "--collision-only/--all-meshes",
    default=True,
    help="Simplify only collision meshes or all meshes (default: collision-only)",
)
@click.option(
    "--no-update",
    is_flag=True,
    help="Don't create updated URDF file (only generate simplified meshes)",
)
def simplify(urdf_file: Path, offset: float, collision_only: bool, no_update: bool):
    """Simplify collision meshes in a URDF file.

    This command processes all mesh references in a URDF/XACRO file and creates
    simplified collision-optimized versions using convex decomposition. The
    simplified meshes are suitable for physics simulation and collision detection.

    The original meshes are preserved and new "simplified_*.stl" files are created.
    By default, a new URDF file with "_simplified" suffix is created that references
    the simplified meshes.

    Examples:
        step-to-scene simplify robot_cell_converted.urdf
        step-to-scene simplify robot.urdf --offset 10.0
        step-to-scene simplify robot.urdf --all-meshes
        step-to-scene simplify robot.urdf --no-update
    """
    click.echo(f"Simplifying meshes in URDF: {urdf_file}")
    click.echo(f"Offset: {offset}mm")
    click.echo(f"Mode: {'Collision only' if collision_only else 'All meshes'}")

    try:
        from step_to_scene.simplify import simplify_urdf_meshes

        def progress_callback(msg: str):
            click.echo(msg)

        simplify_urdf_meshes(
            urdf_path=urdf_file,
            offset=offset,
            update_urdf=not no_update,
            collision_only=collision_only,
            progress_callback=progress_callback,
        )

    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        import traceback
        traceback.print_exc()
        raise click.Abort()


def _print_assembly_tree(assembly, indent=0):
    """Print assembly tree recursively."""
    prefix = "  " * indent + ("└─ " if indent > 0 else "")
    origin_marker = " [ORIGIN]" if assembly.is_origin else ""
    click.echo(f"{prefix}{assembly.name} (ID: {assembly.id}){origin_marker}")

    for child in assembly.children:
        _print_assembly_tree(child, indent + 1)


if __name__ == "__main__":
    main()
