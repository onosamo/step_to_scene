"""Command-line interface for step-to-scene."""

from pathlib import Path
from typing import Annotated

import typer

from step_to_scene import __version__
from step_to_scene.exporters import get_exporter, get_potential_base_links
from step_to_scene.parser import StepParser
from step_to_scene.tui import run_explorer

app = typer.Typer(
    help="""CLI tool to extract static collision geometry from STEP files to URDF/XACRO/SDF.

This tool converts large robotic cells from STEP files into robot description
formats, focusing on extracting static collision geometry. The exported models
represent static obstacles/environment that users can later replace with proper
robot descriptions.

Units are automatically detected and converted to meters if needed."""
)


def version_callback(value: bool):
    """Print version and exit."""
    if value:
        typer.echo(f"step-to-scene version: {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=version_callback,
            is_eager=True,
            help="Show version and exit",
        ),
    ] = None,
):
    """CLI tool for step-to-scene."""
    pass


@app.command()
def explore(
    step_file: Annotated[Path, typer.Argument(exists=True, help="Path to STEP file")],
):
    """Explore STEP file assemblies interactively.

    Opens an interactive TUI (Text User Interface) to browse the assembly
    structure of a STEP file. You can navigate the tree, select assemblies,
    and export them as static collision geometry to URDF, XACRO, or SDF formats.

    The exported files will contain placeholder collision geometry that should
    be replaced with actual mesh files or proper dimensions based on the STEP data.

    Example:
        step-to-scene explore robot_cell.step
    """
    typer.echo(f"Loading STEP file: {step_file}")

    try:
        run_explorer(step_file)
    except Exception as e:
        typer.echo(f"Error: {str(e)}", err=True)
        raise typer.Exit(1) from e


@app.command()
def export(
    step_file: Annotated[Path, typer.Argument(exists=True, help="Path to STEP file")],
    format: Annotated[
        str,
        typer.Option(
            "-f",
            "--format",
            help="Output format",
            case_sensitive=False,
        ),
    ] = "urdf",
    output: Annotated[
        Path | None,
        typer.Option(
            "-o",
            "--output",
            help="Output file path (default: <input_name>_converted.<format>)",
        ),
    ] = None,
    base_link: Annotated[
        str | None,
        typer.Option(
            "-b",
            "--base-link",
            help="Name to use for the base/reference link (default: 'world' or auto-detected origin)",
        ),
    ] = None,
    list_origins: Annotated[
        bool,
        typer.Option(
            "--list-origins", help="List potential origin/base link candidates and exit"
        ),
    ] = False,
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
    # Validate format
    if format.lower() not in ["urdf", "xacro", "sdf"]:
        typer.echo(
            f"Error: Invalid format '{format}'. Must be one of: urdf, xacro, sdf",
            err=True,
        )
        raise typer.Exit(1)

    format = format.lower()
    typer.echo(f"Loading STEP file: {step_file}")

    try:
        # Parse STEP file
        parser = StepParser(step_file)
        assemblies = parser.parse()

        if not assemblies:
            typer.echo("No assemblies found in STEP file.", err=True)
            raise typer.Exit(1)

        # Get unit information
        unit_name, unit_scale = parser.get_unit_info()
        typer.echo(f"Detected units: {unit_name} (scale to meters: {unit_scale})")

        if unit_scale != 1.0:
            typer.echo(
                f"[WARNING] Units will be converted to meters (scale factor: {unit_scale})"
            )

        typer.echo(f"Found {len(assemblies)} top-level assemblies")

        # List potential origins if requested
        potential_origins = get_potential_base_links(assemblies)
        if list_origins:
            if potential_origins:
                typer.echo("\nPotential origin/base_link candidates:")
                for origin in potential_origins:
                    typer.echo(f"  - {origin.name} (ID: {origin.id})")
            else:
                typer.echo("\nNo origin candidates found. Using default 'world'.")
            return

        # Determine base_link name
        if base_link is None:
            if potential_origins:
                base_link = potential_origins[0].name
                typer.echo(f"Auto-detected base_link: '{base_link}'")
            else:
                base_link = "world"
                typer.echo(f"Using default base_link: '{base_link}'")
        else:
            typer.echo(f"Using specified base_link: '{base_link}'")

        # Determine output path
        if output is None:
            output = step_file.parent / f"{step_file.stem}_converted.{format}"

        # Export
        typer.echo(
            f"Exporting static collision geometry to {format.upper()} format: {output}"
        )
        exporter = get_exporter(format)
        exporter.step_file = step_file  # Set step file for mesh export
        exporter.export(
            assemblies, output, base_link_name=base_link, unit_scale=unit_scale
        )

        typer.echo(f"✓ Successfully exported to {output}")

        # Check if mesh was generated
        mesh_dir = output.parent / f"{output.stem}_meshes"
        if mesh_dir.exists():
            stl_files = list(mesh_dir.glob("*.stl"))
            if stl_files:
                total_size = sum(f.stat().st_size for f in stl_files) / (
                    1024 * 1024
                )  # MB
                typer.echo(f"✓ Exported STL mesh ({total_size:.1f} MB) to {mesh_dir}")

    except Exception as e:
        typer.echo(f"Error: {str(e)}", err=True)
        raise typer.Exit(1) from e


@app.command()
def visualize(
    urdf_file: Annotated[
        Path, typer.Argument(exists=True, help="Path to URDF/XACRO file")
    ],
    simplified: Annotated[
        bool,
        typer.Option("--simplified", help="Visualize simplified version if available"),
    ] = False,
):
    """Visualize exported URDF/XACRO file with 3D viewer.

    Opens a 3D visualization of the URDF/XACRO file with all included meshes
    and transformations applied. Useful for verifying the export result.

    If --simplified flag is used, will visualize the simplified version
    (with _simplified suffix) if it exists.

    Examples:
        step-to-scene visualize robot_cell_converted.xacro
        step-to-scene visualize robot_cell_converted.xacro --simplified
    """
    # Check for simplified version if requested
    if simplified:
        simplified_file = urdf_file.with_name(
            f"{urdf_file.stem}_simplified{urdf_file.suffix}"
        )
        if simplified_file.exists():
            urdf_file = simplified_file
            typer.echo(f"Loading simplified URDF file: {urdf_file}")
        else:
            typer.echo(f"Simplified version not found at: {simplified_file}")
            typer.echo(f"Loading original URDF file: {urdf_file}")
    else:
        typer.echo(f"Loading URDF file: {urdf_file}")

    try:
        from step_to_scene.visualizer import visualize_urdf

        visualize_urdf(urdf_file)
    except Exception as e:
        typer.echo(f"Error: {str(e)}", err=True)
        raise typer.Exit(1) from e


@app.command()
def list_assemblies(
    step_file: Annotated[Path, typer.Argument(exists=True, help="Path to STEP file")],
):
    """List all assemblies in a STEP file.

    Displays a hierarchical list of all assemblies and parts found in the
    STEP file without opening the interactive TUI. Also shows unit information
    and potential origin candidates.

    Example:
        step-to-scene list-assemblies robot_cell.step
    """
    typer.echo(f"Loading STEP file: {step_file}")

    try:
        # Parse STEP file
        parser = StepParser(step_file)
        assemblies = parser.parse()

        # Get unit information
        unit_name, unit_scale = parser.get_unit_info()
        typer.echo(f"Detected units: {unit_name} (scale to meters: {unit_scale})")

        if unit_scale != 1.0:
            typer.echo(
                f"[WARNING] Units will be converted to meters (scale factor: {unit_scale})"
            )

        if not assemblies:
            typer.echo("\nNo assemblies found in STEP file.")
            return

        typer.echo(f"\nFound {len(assemblies)} top-level assemblies:\n")

        # Display assemblies
        for assembly in assemblies:
            _print_assembly_tree(assembly, indent=0)

        # Show potential origin candidates
        potential_origins = get_potential_base_links(assemblies)
        if potential_origins:
            typer.echo("\n" + "=" * 50)
            typer.echo("Potential origin/base_link candidates:")
            for origin in potential_origins:
                typer.echo(f"  [ORIGIN] {origin.name} (ID: {origin.id})")
            typer.echo(
                "\nUse --base-link option with export command to specify which to use."
            )

    except Exception as e:
        typer.echo(f"Error: {str(e)}", err=True)
        raise typer.Exit(1) from e


@app.command()
def simplify(
    urdf_file: Annotated[
        Path, typer.Argument(exists=True, help="Path to URDF/XACRO file")
    ],
    offset: Annotated[
        float,
        typer.Option(
            "--offset", help="Offset distance for collision mesh simplification (mm)"
        ),
    ] = 6.0,
    collision_only: Annotated[
        bool,
        typer.Option(
            "--collision-only/--all-meshes",
            help="Simplify only collision meshes or all meshes",
        ),
    ] = True,
    no_update: Annotated[
        bool,
        typer.Option(
            "--no-update",
            help="Don't create updated URDF file (only generate simplified meshes)",
        ),
    ] = False,
):
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
    typer.echo(f"Simplifying meshes in URDF: {urdf_file}")
    typer.echo(f"Offset: {offset}mm")
    typer.echo(f"Mode: {'Collision only' if collision_only else 'All meshes'}")

    try:
        from step_to_scene.simplify import simplify_urdf_meshes

        def progress_callback(msg: str):
            typer.echo(msg)

        simplify_urdf_meshes(
            urdf_path=urdf_file,
            offset=offset,
            update_urdf=not no_update,
            collision_only=collision_only,
            progress_callback=progress_callback,
        )

    except Exception as e:
        typer.echo(f"Error: {str(e)}", err=True)
        import traceback

        traceback.print_exc()
        raise typer.Exit(1) from e


def _print_assembly_tree(assembly, indent=0):
    """Print assembly tree recursively."""
    prefix = "  " * indent + ("└─ " if indent > 0 else "")
    origin_marker = " [ORIGIN]" if assembly.is_origin else ""
    typer.echo(f"{prefix}{assembly.name} (ID: {assembly.id}){origin_marker}")

    for child in assembly.children:
        _print_assembly_tree(child, indent + 1)


if __name__ == "__main__":
    app()
