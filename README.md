# step_to_scene

CLI tool to extract static collision geometry from STEP files for robotic cells and convert them to URDF/XACRO/SDF formats.

## Overview

This tool helps you convert large robotic cells from STEP CAD files into robot description formats (URDF/XACRO/SDF). The primary focus is on **extracting static collision geometry** that represents the environment, obstacles, and static parts of the cell.

The exported models contain placeholder collision geometry that users should replace with actual mesh files or proper dimensions based on the STEP data. Robot parts should be replaced with proper kinematic descriptions afterwards.

## Features

- 🔍 **Interactive TUI** for exploring STEP file assemblies
- 📦 **Assembly Selection** - Choose specific parts/assemblies to export
- 🛡️ **Collision-Focused Export** - Generates static collision geometry for environment modeling
- 🤖 **Multiple Output Formats** - URDF, XACRO, and SDF support
- 🌲 **Hierarchical View** - Browse assemblies like a file explorer
- ⚡ **Batch Export** - Convert entire STEP files without interaction
- 📏 **Automatic Unit Conversion** - Detects and converts mm/cm/inches to meters
- 🎯 **Smart Base Link Detection** - Auto-detects origin/reference frames from assembly names

## Installation

```bash
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

## Usage

### Interactive Explorer

Open an interactive TUI to browse and select assemblies:

```bash
step-to-scene explore robot_cell.step
```

**Key Bindings:**
- `↑/↓` - Navigate tree
- `Enter` - Select/deselect assembly
- `X` - Exclude/include assembly from export
- `E` - Export selected assemblies as static collision geometry
- `A` - Select all assemblies
- `C` - Clear selection
- `Q` - Quit

The exported files will contain placeholder collision geometry that should be replaced with actual meshes.

**Excluding Assemblies:**
You can mark assemblies for exclusion with the `X` key. Excluded assemblies will not be included in the exported STEP file or meshes. This is useful when:
- You want to remove certain parts from the environment model
- You need to simplify the scene by excluding complex assemblies
- You want to export parent assemblies without specific children

The exclude feature works by creating a temporary STEP file with the excluded parts removed before export.

### Batch Export

Export all assemblies as static collision geometry:

```bash
# Export to URDF (default)
step-to-scene export robot_cell.step

# Export to XACRO
step-to-scene export robot_cell.step --format xacro

# Export to SDF with custom output path
step-to-scene export robot_cell.step --format sdf --output environment.sdf

# Specify a custom base_link (reference frame)
step-to-scene export robot_cell.step --base-link robot_origin

# List potential origin candidates
step-to-scene export robot_cell.step --list-origins
```

**Unit Conversion:**
The tool automatically detects units from the STEP file:
- Millimeters (mm) → Converted to meters (scale: 0.001)
- Centimeters (cm) → Converted to meters (scale: 0.01)
- Inches (in) → Converted to meters (scale: 0.0254)
- Meters (m) → No conversion needed

**Base Link Selection:**
The tool can auto-detect potential origin/base_link candidates by looking for parts with names containing:
- `origin`, `base`, `world`, `root`, `reference`, or `frame`

Use `--list-origins` to see all candidates, then specify your choice with `--base-link`.

**Important:** The exported files contain placeholder collision geometry. You should:
1. Replace collision geometries with actual mesh files from the STEP data
2. Update positions/orientations based on the actual STEP geometry
3. Replace robot parts with proper kinematic URDF/SDF descriptions

### List Assemblies

Display all assemblies in a STEP file:

```bash
step-to-scene list-assemblies robot_cell.step
```

## Workflow

1. **Explore** your STEP file to understand the assembly structure
2. **Select** the parts you want to export (or export all)
3. **Export** to your desired format (URDF/XACRO/SDF)
4. **Replace** placeholder geometries with actual meshes or dimensions
5. **Replace** robot parts with proper kinematic descriptions

## Output Formats

### URDF (Unified Robot Description Format)
Standard format for ROS robots. Exported files contain:
- Static collision geometry for environment parts
- Fixed joints (all parts are static)
- Placeholder box geometries to be replaced with meshes
- Minimal inertial properties

### XACRO (XML Macros)
Enhanced URDF with macros and properties:
- Parameterized collision geometries for easy updates
- XACRO properties for dimensions and masses
- Reusable macros for common collision patterns
- Fixed joints (all parts are static)

### SDF (Simulation Description Format)
Format used by Gazebo simulator:
- Static model flag set to true
- Collision and visual geometry
- Surface friction properties
- Placeholder geometries to be replaced

All formats focus on **static collision representation** suitable for environment modeling in robot simulations.

## Development

### Running Tests

```bash
pytest
```

### Code Formatting

```bash
black step_to_scene
```

### Linting

```bash
ruff check step_to_scene
```

## License

MIT License - see LICENSE file for details.