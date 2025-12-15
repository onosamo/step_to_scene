"""Interactive TUI for browsing and selecting assemblies."""

from pathlib import Path
from typing import List

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Label, Tree
from textual.widgets.tree import TreeNode

from step_to_scene.exporters import get_exporter
from step_to_scene.parser import StepAssembly, StepParser


class StepExplorerApp(App):
    """TUI application for exploring STEP assemblies."""

    CSS = """
    Screen {
        layout: vertical;
        background: #fdf6e3;
    }

    #title {
        width: 100%;
        height: 3;
        content-align: center middle;
        background: #268bd2;
        color: #fdf6e3;
        text-style: bold;
    }

    #main_container {
        width: 100%;
        height: 1fr;
    }

    #tree_container {
        width: 100%;
        height: 1fr;
        border: solid #93a1a1;
        background: #fdf6e3;
    }

    #info_panel {
        width: 100%;
        height: 8;
        border: solid #6c71c4;
        background: #eee8d5;
        padding: 1;
        color: #586e75;
    }

    #button_container {
        width: 100%;
        height: auto;
        align: center middle;
        padding: 1;
        background: #fdf6e3;
    }

    Button {
        margin: 0 1;
    }

    Tree {
        height: 100%;
        width: 100%;
        background: #fdf6e3;
        color: #657b83;
    }
    
    Label {
        color: #586e75;
    }
    
    Header {
        background: #268bd2;
        color: #fdf6e3;
    }
    
    Footer {
        background: #93a1a1;
        color: #002b36;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("e", "export", "Export Selected"),
        ("a", "select_all", "Select All"),
        ("c", "clear_selection", "Clear Selection"),
        ("h", "toggle_hide_empty", "Hide/Show Empty"),
    ]

    def __init__(self, step_file: Path):
        super().__init__()
        self.step_file = step_file
        self.parser = StepParser(step_file)
        self.assemblies: List[StepAssembly] = []
        self.unit_scale = 1.0
        self.unit_name = "UNKNOWN"
        self.base_link_name = "world"
        self.selected_assemblies: set[str] = set()  # Track selections at app level
        self.hide_empty_assemblies = False  # Flag to hide empty top-level assemblies

    def compose(self) -> ComposeResult:
        """Compose the application UI."""
        yield Header()

        # Title
        yield Label(f"STEP File Explorer: {self.step_file.name}", id="title")

        # Main container
        with Vertical(id="main_container"):
            # Tree container
            with Container(id="tree_container"):
                yield Tree("STEP Assemblies", id="assembly_tree")

            # Info panel
            with Container(id="info_panel"):
                yield Label(
                    "Navigate: ↑/↓ arrows | Select: Enter | Export: E | Hide Empty: H | Quit: Q", id="info_label"
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
            
            # Rebuild the tree with parsed assemblies
            self._rebuild_tree()
                
            self.update_selection_info()
            
            # Show unit information
            if self.unit_scale != 1.0:
                self.notify(
                    f"Units detected: {self.unit_name} (will convert to meters: scale={self.unit_scale})",
                    severity="information"
                )
        except Exception as e:
            self.exit(message=f"Error parsing STEP file: {str(e)}")
    
    def _rebuild_tree(self):
        """Rebuild the assembly tree based on current filter settings."""
        tree = self.query_one("#assembly_tree", Tree)
        tree.clear()
        tree.root.expand()
        
        # Track added IDs to prevent duplicates
        added_ids = set()
        
        # Add assemblies to tree
        for assembly in self.assemblies:
            self._add_assembly_to_tree(tree.root, assembly, added_ids)
    
    def _has_nested_parts(self, assembly: StepAssembly) -> bool:
        """Check if assembly has any nested objects (children)."""
        # An assembly has nested parts if it has at least one child
        # This will show assemblies with children and hide leaf nodes
        if len(assembly.children) == 0:
            return False
        
        # If it has children, recursively check if any path leads to actual nested content
        # Even if children are empty, we still consider it as having nested structure
        return True
    
    def _add_assembly_to_tree(self, parent_node: TreeNode, assembly: StepAssembly, added_ids: set = None):
        """Recursively add assembly and its children to the tree."""
        if added_ids is None:
            added_ids = set()
        
        # Skip if already added (prevents duplicates)
        if assembly.id in added_ids:
            return
        
        # When hiding empty assemblies, skip assemblies that don't have nested parts
        if self.hide_empty_assemblies and not self._has_nested_parts(assembly):
            return
        
        label = f"{assembly.name} (ID: {assembly.id})"
        node = parent_node.add(label, data=assembly.id)
        added_ids.add(assembly.id)

        # Add children
        for child in assembly.children:
            self._add_assembly_to_tree(node, child, added_ids)

    def update_selection_info(self):
        """Update the selection information label."""
        count = len(self.selected_assemblies)
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
        selected_ids = self.selected_assemblies
        if not selected_ids:
            self.notify("No assemblies selected. Exporting all as static collision.", severity="warning")
            selected_assemblies = self.assemblies
        else:
            # Find selected assemblies - use dict to avoid duplicates by ID
            assemblies_by_id = {}
            for assembly in self.assemblies:
                if assembly.id in selected_ids:
                    assemblies_by_id[assembly.id] = assembly
                # Also check children
                for child in self._find_selected_children(assembly, selected_ids):
                    assemblies_by_id[child.id] = child
            
            selected_assemblies = list(assemblies_by_id.values())

        if not selected_assemblies:
            self.notify("No assemblies to export.", severity="error")
            return

        # Export
        try:
            output_file = self.step_file.parent / f"{self.step_file.stem}_converted.{format}"
            exporter = get_exporter(format)
            exporter.step_file = self.step_file  # Set step file for mesh export
            exporter.export(
                selected_assemblies, 
                output_file, 
                base_link_name=self.base_link_name,
                unit_scale=self.unit_scale
            )
            unit_msg = f" (units converted: {self.unit_scale}x)" if self.unit_scale != 1.0 else ""
            self.notify(
                f"Exported to {output_file}{unit_msg}.", 
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
        # Get all assembly IDs
        all_ids = set()
        for assembly in self.assemblies:
            all_ids.add(assembly.id)
            all_ids.update(self._get_all_child_ids(assembly))

        self.selected_assemblies = all_ids
        
        # Update tree labels to show selection
        tree = self.query_one("#assembly_tree", Tree)
        self._update_tree_labels(tree.root, all_ids, add_marker=True)
        
        self.update_selection_info()
        self.notify("All assemblies selected", severity="information")

    def action_clear_selection(self) -> None:
        """Clear all selections."""
        self.selected_assemblies.clear()
        
        # Update tree labels to remove selection markers
        tree = self.query_one("#assembly_tree", Tree)
        self._update_tree_labels(tree.root, set(), add_marker=False)
        
        self.update_selection_info()
        self.notify("Selection cleared", severity="information")
    
    def action_toggle_hide_empty(self) -> None:
        """Toggle hiding assemblies without nested parts."""
        self.hide_empty_assemblies = not self.hide_empty_assemblies
        self._rebuild_tree()
        
        if self.hide_empty_assemblies:
            self.notify("Hiding assemblies without nested parts", severity="information")
        else:
            self.notify("Showing all assemblies", severity="information")
    
    def _update_tree_labels(self, node: TreeNode, selected_ids: set[str], add_marker: bool):
        """Recursively update tree labels to show/hide selection markers."""
        for child in node.children:
            if child.data:
                current_label = str(child.label)
                if add_marker and child.data in selected_ids:
                    if not current_label.startswith("[✓] "):
                        child.label = f"[✓] {current_label}"
                elif not add_marker:
                    if current_label.startswith("[✓] "):
                        child.label = current_label.replace("[✓] ", "")
            # Recurse into children
            self._update_tree_labels(child, selected_ids, add_marker)

    def _get_all_child_ids(self, assembly: StepAssembly) -> set[str]:
        """Get all child IDs recursively."""
        ids = set()
        for child in assembly.children:
            ids.add(child.id)
            ids.update(self._get_all_child_ids(child))
        return ids

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Update selection info when tree node is selected."""
        if event.node.data:
            assembly_id = event.node.data
            if assembly_id in self.selected_assemblies:
                self.selected_assemblies.remove(assembly_id)
                current_label = str(event.node.label)
                event.node.label = current_label.replace("[✓] ", "")
            else:
                self.selected_assemblies.add(assembly_id)
                # Update label to show selection
                current_label = str(event.node.label)
                if not current_label.startswith("[✓] "):
                    event.node.label = f"[✓] {current_label}"
        
        self.update_selection_info()


def run_explorer(step_file: Path) -> None:
    """Run the STEP explorer TUI."""
    app = StepExplorerApp(step_file)
    app.run()
