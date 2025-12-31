# step-to-scene

CLI tool to convert STEP CAD files to robot description formats (URDF/XACRO/SDF) with mesh extraction and collision geometry simplification.

## Overview

**step-to-scene** helps you convert robotic cells and assemblies from STEP CAD files into robot description formats. It extracts geometry, creates STL meshes, and generates properly structured URDF/XACRO files ready for use in ROS and robot simulators.

Key capabilities:
- Parse STEP file assembly hierarchy
- Export individual assemblies as separate URDF files with STL meshes
- Generate a main XACRO file that includes all parts with proper transforms
- Simplify collision meshes using convex hull decomposition (VHACD)
- Create distributable archives of your converted scenes

## Installation

Install from PyPI:

```bash
pip install step-to-scene
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install step-to-scene
```

### Requirements

- Python 3.12+
- [CadQuery](https://cadquery.readthedocs.io/) with OCC (for STEP parsing and mesh export)

## Usage

### Interactive Explorer (TUI)

Launch the interactive terminal UI to browse assemblies, select parts for export, and manage the conversion process:

```bash
step-to-scene explore robot_cell.step
```

**TUI Features:**
- Browse assembly hierarchy as a tree
- Select/deselect assemblies for export
- Exclude assemblies from the exported STEP (removes them entirely)
- Export selected assemblies to URDF/XACRO with STL meshes
- Visualize exported scenes (requires trimesh)
- Simplify collision meshes with convex decomposition
- Create archives for distribution

**Key Bindings:**
| Key | Action |
|-----|--------|
| `Up/Down` | Navigate tree |
| `Enter` | Toggle selection |
| `X` | Toggle exclude (strikethrough) |
| `E` | Export selected assemblies |
| `A` | Select all |
| `C` | Clear selection |
| `Q` | Quit |

### Batch Export

Export all assemblies without the interactive UI:

```bash
# Export to XACRO (default creates main.xacro + parts/*.urdf + meshes/*.stl)
step-to-scene export robot_cell.step

# Export to specific format
step-to-scene export robot_cell.step --format urdf
step-to-scene export robot_cell.step --format sdf

# Custom output path
step-to-scene export robot_cell.step --output my_scene.xacro

# Specify base link name
step-to-scene export robot_cell.step --base-link robot_base
```

### List Assemblies

View the assembly structure without exporting:

```bash
step-to-scene list-assemblies robot_cell.step
```

### Visualize

View exported URDF/XACRO files in 3D (requires trimesh):

```bash
step-to-scene visualize scene.xacro

# View simplified version
step-to-scene visualize scene.xacro --simplified
```

### Simplify Meshes

Create simplified collision meshes using convex hull decomposition:

```bash
step-to-scene simplify scene.xacro

# Custom offset for collision padding
step-to-scene simplify scene.xacro --offset 10.0
```

### Simplify Single Mesh

Simplify a single mesh file without needing a URDF/XACRO:

```bash
step-to-scene simplify-mesh part.stl

# Custom offset and output path
step-to-scene simplify-mesh part.stl --offset 10.0 -o collision_part.stl

# Show visualization of original vs simplified
step-to-scene simplify-mesh part.stl --visualize
```

### Create Archives

Package exported files into distributable archives:

```bash
step-to-scene archive scene.xacro

# Exclude STEP file from archive
step-to-scene archive scene.xacro --no-step

# Skip simplified archive
step-to-scene archive scene.xacro --no-simplified
```

## Output Structure

After export, you get:

```
output_dir/
├── scene.xacro              # Main file (includes all parts)
├── scene_parts/             # Individual URDF files
│   ├── part_a.urdf
│   ├── part_b.urdf
│   └── ...
├── scene_meshes/            # STL mesh files
│   ├── part_a.stl
│   ├── part_b.stl
│   └── ...
└── scene_archive.tar.gz     # Optional: distributable archive
```

After simplification:

```
output_dir/
├── scene_simplified.xacro   # Simplified version
├── scene_parts/
│   ├── part_a_simplified.urdf
│   └── ...
└── scene_meshes/
    ├── simplified_part_a.stl
    └── ...
```

## Unit Conversion

The tool automatically detects units from STEP files and converts to meters:

| Source Unit | Scale Factor |
|-------------|-------------|
| Millimeters | 0.001 |
| Centimeters | 0.01 |
| Inches | 0.0254 |
| Meters | 1.0 |

## Development

### Setup

```bash
git clone https://github.com/szobov/step_to_scene.git
cd step_to_scene
uv sync --group dev
```

### Run Tests

```bash
uv run pytest
```

### Linting and Formatting

```bash
uv run ruff check step_to_scene/ tests/
uv run ruff format step_to_scene/ tests/
```

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.
