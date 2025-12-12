"""Interactive TUI for browsing and selecting assemblies."""

from pathlib import Path
from typing import List

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Label, Static, Tree
from textual.widgets.tree import TreeNode

from step_to_scene.exporters import get_exporter
from step_to_scene.parser import StepAssembly, StepParser


class AssemblyTree(Static):
    """A widget to display the assembly tree."""

    def __init__(self, assemblies: List[StepAssembly]):
        super().__init__()
        self.assemblies = assemblies
        self.selected_assemblies: set[str] = set()

    def compose(self) -> ComposeResult:
        """Create the tree widget."""
        tree: Tree[str] = Tree("STEP Assemblies", id="assembly_tree")
        tree.root.expand()

        # Add assemblies to tree
        for assembly in self.assemblies:
            self._add_assembly_to_tree(tree.root, assembly)

        yield tree

    def _add_assembly_to_tree(self, parent_node: TreeNode, assembly: StepAssembly):
        """Recursively add assembly and its children to the tree."""
        label = f"{assembly.name} (ID: {assembly.id})"
        node = parent_node.add(label, data=assembly.id)

        # Add children
        for child in assembly.children:
            self._add_assembly_to_tree(node, child)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle node selection."""
        if event.node.data:
            assembly_id = event.node.data
            if assembly_id in self.selected_assemblies:
                self.selected_assemblies.remove(assembly_id)
                event.node.label = str(event.node.label).replace("[✓] ", "")
            else:
                self.selected_assemblies.add(assembly_id)
                # Update label to show selection
                current_label = str(event.node.label)
                if not current_label.startswith("[✓] "):
                    event.node.label = f"[✓] {current_label}"


class StepExplorerApp(App):
    """TUI application for exploring STEP assemblies."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #title {
        width: 100%;
        height: 3;
        content-align: center middle;
        background: $boost;
        color: $text;
        text-style: bold;
    }

    #main_container {
        width: 100%;
        height: 1fr;
    }

    #tree_container {
        width: 100%;
        height: 1fr;
        border: solid $primary;
    }

    #info_panel {
        width: 100%;
        height: 8;
        border: solid $accent;
        padding: 1;
    }

    #button_container {
        width: 100%;
        height: auto;
        align: center middle;
        padding: 1;
    }

    Button {
        margin: 0 1;
    }

    Tree {
        height: 100%;
        width: 100%;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("e", "export", "Export Selected"),
        ("a", "select_all", "Select All"),
        ("c", "clear_selection", "Clear Selection"),
    ]

    def __init__(self, step_file: Path):
        super().__init__()
        self.step_file = step_file
        self.parser = StepParser(step_file)
        self.assemblies: List[StepAssembly] = []
        self.assembly_tree_widget = None
        self.unit_scale = 1.0
        self.unit_name = "UNKNOWN"
        self.base_link_name = "world"

    def compose(self) -> ComposeResult:
        """Compose the application UI."""
        yield Header()

        # Title
        yield Label(f"STEP File Explorer: {self.step_file.name}", id="title")

        # Main container
        with Vertical(id="main_container"):
            # Tree container
            with Container(id="tree_container"):
                self.assembly_tree_widget = AssemblyTree(self.assemblies)
                yield self.assembly_tree_widget

            # Info panel
            with Container(id="info_panel"):
                yield Label(
                    "Navigate: ↑/↓ arrows | Select: Enter | Export: E | Quit: Q", id="info_label"
                )
                yield Label(
                    "Select assemblies to export as static collision geometry. Selected items marked with [✓]",
                    id="help_label",
                )
                yield Label("No assemblies selected", id="selection_info")

            # Buttons
            with Horizontal(id="button_container"):
                yield Button("Export as URDF", id="export_urdf", variant="primary")
                yield Button("Export as XACRO", id="export_xacro", variant="primary")
                yield Button("Export as SDF", id="export_sdf", variant="primary")
                yield Button("Quit", id="quit", variant="error")

        yield Footer()

    def on_mount(self) -> None:
        """Parse the STEP file when the app starts."""
        try:
            self.assemblies = self.parser.parse()
            
            # Get unit information
            self.unit_name, self.unit_scale = self.parser.get_unit_info()
            
            # Get potential base links
            from step_to_scene.exporters import get_potential_base_links
            potential_origins = get_potential_base_links(self.assemblies)
            if potential_origins:
                self.base_link_name = potential_origins[0].name
            
            if self.assembly_tree_widget:
                self.assembly_tree_widget.assemblies = self.assemblies
                # Rebuild the tree
                self.refresh()
            self.update_selection_info()
            
            # Show unit information
            if self.unit_scale != 1.0:
                self.notify(
                    f"Units detected: {self.unit_name} (will convert to meters: scale={self.unit_scale})",
                    severity="information"
                )
        except Exception as e:
            self.exit(message=f"Error parsing STEP file: {str(e)}")

    def update_selection_info(self):
        """Update the selection information label."""
        if self.assembly_tree_widget:
            count = len(self.assembly_tree_widget.selected_assemblies)
            info_label = self.query_one("#selection_info", Label)
            if count == 0:
                info_label.update("No assemblies selected")
            elif count == 1:
                info_label.update("1 assembly selected")
            else:
                info_label.update(f"{count} assemblies selected")

    @on(Button.Pressed, "#export_urdf")
    def export_urdf(self) -> None:
        """Export selected assemblies to URDF."""
        self._export("urdf")

    @on(Button.Pressed, "#export_xacro")
    def export_xacro(self) -> None:
        """Export selected assemblies to XACRO."""
        self._export("xacro")

    @on(Button.Pressed, "#export_sdf")
    def export_sdf(self) -> None:
        """Export selected assemblies to SDF."""
        self._export("sdf")

    @on(Button.Pressed, "#quit")
    def quit_app(self) -> None:
        """Quit the application."""
        self.exit()

    def _export(self, format: str):
        """Export selected assemblies to the specified format as static collision geometry."""
        if not self.assembly_tree_widget:
            return

        selected_ids = self.assembly_tree_widget.selected_assemblies
        if not selected_ids:
            self.notify("No assemblies selected. Exporting all as static collision.", severity="warning")
            selected_assemblies = self.assemblies
        else:
            # Find selected assemblies
            selected_assemblies = []
            for assembly in self.assemblies:
                if assembly.id in selected_ids:
                    selected_assemblies.append(assembly)
                # Also check children
                selected_assemblies.extend(self._find_selected_children(assembly, selected_ids))

        if not selected_assemblies:
            self.notify("No assemblies to export.", severity="error")
            return

        # Export
        try:
            output_file = self.step_file.parent / f"{self.step_file.stem}_converted.{format}"
            exporter = get_exporter(format)
            exporter.export(
                selected_assemblies, 
                output_file, 
                base_link_name=self.base_link_name,
                unit_scale=self.unit_scale
            )
            unit_msg = f" (units converted: {self.unit_scale}x)" if self.unit_scale != 1.0 else ""
            self.notify(
                f"Exported to {output_file}{unit_msg}. Replace placeholder geometries with actual meshes.", 
                severity="information",
                timeout=5
            )
        except Exception as e:
            self.notify(f"Export failed: {str(e)}", severity="error")

    def _find_selected_children(
        self, assembly: StepAssembly, selected_ids: set[str]
    ) -> List[StepAssembly]:
        """Recursively find selected children."""
        selected = []
        for child in assembly.children:
            if child.id in selected_ids:
                selected.append(child)
            selected.extend(self._find_selected_children(child, selected_ids))
        return selected

    def action_export(self) -> None:
        """Action to show export options."""
        self.notify("Choose an export format using the buttons below", severity="information")

    def action_select_all(self) -> None:
        """Select all assemblies."""
        if self.assembly_tree_widget:
            # Get all assembly IDs
            all_ids = set()
            for assembly in self.assemblies:
                all_ids.add(assembly.id)
                all_ids.update(self._get_all_child_ids(assembly))

            self.assembly_tree_widget.selected_assemblies = all_ids
            self.update_selection_info()
            self.notify("All assemblies selected", severity="information")

    def action_clear_selection(self) -> None:
        """Clear all selections."""
        if self.assembly_tree_widget:
            self.assembly_tree_widget.selected_assemblies.clear()
            self.update_selection_info()
            self.notify("Selection cleared", severity="information")

    def _get_all_child_ids(self, assembly: StepAssembly) -> set[str]:
        """Get all child IDs recursively."""
        ids = set()
        for child in assembly.children:
            ids.add(child.id)
            ids.update(self._get_all_child_ids(child))
        return ids

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Update selection info when tree node is selected."""
        self.update_selection_info()


def run_explorer(step_file: Path) -> None:
    """Run the STEP explorer TUI."""
    app = StepExplorerApp(step_file)
    app.run()
