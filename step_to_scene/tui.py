"""Interactive TUI for browsing and selecting assemblies."""

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Tree
from textual.widgets.tree import TreeNode

from step_to_scene.exporters import get_exporter
from step_to_scene.parser import StepAssembly, StepParser


class SimplifyDialog(ModalScreen):
    """Modal dialog for configuring simplify options."""

    CSS = """
    SimplifyDialog {
        align: center middle;
    }

    #dialog {
        width: 60;
        height: 17;
        border: thick #268bd2;
        background: #fdf6e3;
        padding: 1 2;
    }

    #dialog_title {
        width: 100%;
        content-align: center middle;
        text-style: bold;
        color: #268bd2;
        margin-bottom: 1;
    }

    #offset_input {
        width: 100%;
        margin: 1 0;
    }

    #button_row {
        width: 100%;
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label("Configure Mesh Simplification", id="dialog_title")
            yield Label("Offset distance (mm):")
            yield Input(value="6.0", placeholder="6.0", id="offset_input")
            yield Label("Adds clearance around collision meshes.")
            yield Label("Mesh units: mm | Default: 6.0mm ≈ 0.006m")
            with Horizontal(id="button_row"):
                yield Button("Simplify", variant="primary", id="simplify_confirm")
                yield Button("Cancel", variant="error", id="simplify_cancel")

    @on(Button.Pressed, "#simplify_confirm")
    def confirm_simplify(self) -> None:
        """Confirm simplification with the entered offset."""
        offset_input = self.query_one("#offset_input", Input)
        try:
            offset = float(offset_input.value)
            if offset < 0:
                # Use notify from the screen, not app
                self.notify("Offset must be non-negative", severity="error")
                return
            self.dismiss(offset)
        except ValueError:
            self.notify(
                "Invalid offset value. Please enter a number.", severity="error"
            )

    @on(Button.Pressed, "#simplify_cancel")
    def cancel_simplify(self) -> None:
        """Cancel simplification."""
        self.dismiss(None)


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

    #search_container {
        width: 100%;
        height: auto;
        padding: 1;
        background: #eee8d5;
    }

    Input {
        width: 100%;
        border: solid #268bd2;
    }

    #info_panel {
        width: 100%;
        height: 10;
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
    
    #progress_label {
        color: #268bd2;
        text-style: bold;
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
        ("v", "visualize", "Visualize"),
        ("s", "simplify", "Simplify Meshes"),
        ("a", "select_all", "Select All"),
        ("c", "clear_selection", "Clear Selection"),
        ("x", "toggle_exclude", "Exclude/Include"),
        ("h", "toggle_hide_empty", "Hide/Show Empty"),
        ("/", "focus_search", "Search"),
    ]

    def __init__(self, step_file: Path):
        super().__init__()
        self.step_file = step_file
        self.parser = StepParser(step_file)
        self.assemblies: list[StepAssembly] = []
        self.unit_scale = 1.0
        self.unit_name = "UNKNOWN"
        self.base_link_name = "world"
        self.selected_assemblies: set[str] = set()  # Track selections at app level
        self.excluded_assemblies: set[str] = (
            set()
        )  # Track exclusions (parts to skip in export)
        self.hide_empty_assemblies = False  # Flag to hide empty top-level assemblies
        self.search_query = ""  # Current search query

    def compose(self) -> ComposeResult:
        """Compose the application UI."""
        yield Header()

        # Title
        yield Label(f"STEP File Explorer: {self.step_file.name}", id="title")

        # Main container
        with Vertical(id="main_container"):
            # Search container
            with Container(id="search_container"):
                yield Input(
                    placeholder="Search assemblies (fuzzy match)...", id="search_input"
                )

            # Tree container
            with Container(id="tree_container"):
                yield Tree("STEP Assemblies", id="assembly_tree")

            # Info panel
            with Container(id="info_panel"):
                yield Label(
                    "Navigate: ↑/↓ | Select: Enter | Exclude: X | Export: E | Simplify: S | Visualize: V | Search: / | Hide Empty: H | Quit: Q",
                    id="info_label",
                )
                yield Label(
                    "Select assemblies to export as static collision geometry. Selected items marked with [✓], Excluded items marked with [✗]",
                    id="help_label",
                )
                yield Label("No assemblies selected", id="selection_info")
                yield Label("", id="progress_label")

            # Buttons
            with Horizontal(id="button_container"):
                yield Button("Export as URDF", id="export_urdf", variant="primary")
                yield Button("Simplify Meshes", id="simplify_meshes", variant="warning")
                yield Button("Visualize URDF", id="visualize_urdf", variant="success")
                yield Button(
                    "Visualize Simplified", id="visualize_simplified", variant="success"
                )
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
                    severity="information",
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

        # Reapply selection markers to preserve selections
        if self.selected_assemblies:
            self._update_tree_labels(
                tree.root, self.selected_assemblies, add_marker=True
            )

    def _fuzzy_match(self, query: str, text: str) -> bool:
        """Check if query matches text using fuzzy matching.

        Fuzzy matching allows characters to appear in order but not necessarily consecutively.
        Example: "dsp" matches "DMSP-20" and "fdm" matches "Festo_DMSP"
        """
        if not query:
            return True

        query = query.lower()
        text = text.lower()

        # Simple fuzzy match: all query chars must appear in order in text
        query_idx = 0
        for char in text:
            if query_idx < len(query) and char == query[query_idx]:
                query_idx += 1

        return query_idx == len(query)

    def _format_assembly_label(self, assembly: StepAssembly) -> str:
        """Format assembly label with name, description, and ID, including selection/exclusion markers."""
        if assembly.description:
            label = f"{assembly.name} - {assembly.description} (ID: {assembly.id})"
        else:
            label = f"{assembly.name} (ID: {assembly.id})"

        # Add markers for exclusion and selection
        if assembly.id in self.excluded_assemblies:
            label = f"[✗] {label}"
        elif assembly.id in self.selected_assemblies:
            label = f"[✓] {label}"

        return label

    def _assembly_matches_search(self, assembly: StepAssembly) -> bool:
        """Check if assembly or any of its children match the search query."""
        if not self.search_query:
            return True

        # Check if this assembly name matches
        if self._fuzzy_match(self.search_query, assembly.name):
            return True

        # Check if description matches
        if assembly.description and self._fuzzy_match(
            self.search_query, assembly.description
        ):
            return True

        # Check if ID matches
        if self._fuzzy_match(self.search_query, str(assembly.id)):
            return True

        # Check if any child matches (recursive)
        return any(self._assembly_matches_search(child) for child in assembly.children)

    def _has_nested_parts(self, assembly: StepAssembly) -> bool:
        """Check if assembly should be shown when hiding empty assemblies.

        An assembly should be shown if:
        - It has children (is a container with sub-assemblies/parts), OR
        - It is a leaf node (actual part with no children)

        This ensures all actual parts and meaningful assemblies are visible.
        The hide_empty feature is currently not effective since we don't track
        geometry information from STEP files.
        """
        # Always return True for now - show all assemblies/parts
        # The hide_empty feature needs proper geometry tracking to work correctly
        return True

    def _add_assembly_to_tree(
        self, parent_node: TreeNode, assembly: StepAssembly, added_ids: set = None
    ):
        """Recursively add assembly and its children to the tree.

        Note: Does not prevent duplicate IDs because STEP files often reuse
        the same part in multiple locations (e.g., fasteners, connectors).
        Each instance should be shown in its proper location in the hierarchy.
        """
        if added_ids is None:
            added_ids = set()

        # NOTE: Removed duplicate checking because STEP files commonly reuse
        # parts in multiple locations. Each occurrence should be visible in the tree.
        # The parser already handles circular references, so this is safe.

        # When hiding empty assemblies, skip assemblies that don't have nested parts
        if self.hide_empty_assemblies and not self._has_nested_parts(assembly):
            return

        # Apply search filter
        if not self._assembly_matches_search(assembly):
            return

        label = self._format_assembly_label(assembly)
        node = parent_node.add(label, data=assembly.id)
        # Still track added IDs for potential future use, but don't block on duplicates
        added_ids.add(assembly.id)

        # Add children
        for child in assembly.children:
            self._add_assembly_to_tree(node, child, added_ids)

    def update_selection_info(self):
        """Update the selection information label."""
        count = len(self.selected_assemblies)
        excluded_count = len(self.excluded_assemblies)
        info_label = self.query_one("#selection_info", Label)

        if count == 0 and excluded_count == 0:
            info_label.update("No assemblies selected or excluded")
        elif count == 0:
            info_label.update(f"{excluded_count} assemblies excluded")
        elif excluded_count == 0:
            if count == 1:
                info_label.update("1 assembly selected")
            else:
                info_label.update(f"{count} assemblies selected")
        else:
            info_label.update(f"{count} assemblies selected, {excluded_count} excluded")

    @on(Button.Pressed, "#export_urdf")
    async def export_urdf(self) -> None:
        """Export selected assemblies to URDF."""
        await self._export("urdf")

    @on(Button.Pressed, "#visualize_urdf")
    def visualize_urdf(self) -> None:
        """Visualize exported URDF."""
        xacro_file = self.step_file.parent / f"{self.step_file.stem}_converted.xacro"
        if not xacro_file.exists():
            self.notify("Export URDF first before visualizing", severity="error")
            return

        try:
            from step_to_scene.visualizer import visualize_urdf

            visualize_urdf(xacro_file)
        except Exception as e:
            self.notify(f"Visualization failed: {str(e)}", severity="error")

    @on(Button.Pressed, "#visualize_simplified")
    def visualize_simplified(self) -> None:
        """Visualize simplified URDF."""
        xacro_file = self.step_file.parent / f"{self.step_file.stem}_converted.xacro"
        simplified_xacro = xacro_file.with_name(
            f"{xacro_file.stem}_simplified{xacro_file.suffix}"
        )

        if not simplified_xacro.exists():
            self.notify(
                "Simplified URDF not found. Run 'Simplify Meshes' first.",
                severity="error",
            )
            return

        try:
            from step_to_scene.visualizer import visualize_urdf

            visualize_urdf(simplified_xacro)
        except Exception as e:
            self.notify(f"Visualization failed: {str(e)}", severity="error")

    @on(Button.Pressed, "#simplify_meshes")
    def simplify_meshes_button(self) -> None:
        """Simplify meshes in exported URDF."""
        self.run_worker(self._show_simplify_dialog_and_run())

    @on(Button.Pressed, "#quit")
    def quit_app(self) -> None:
        """Quit the application."""
        self.exit()

    async def _export(self, format: str):
        """Export selected assemblies to the specified format as static collision geometry."""
        selected_ids = self.selected_assemblies
        excluded_ids = self.excluded_assemblies.copy()

        if not selected_ids:
            self.notify(
                "No assemblies selected. Exporting all as static collision.",
                severity="warning",
            )
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

        # Filter out explicitly excluded assemblies
        filtered_assemblies = [
            a for a in selected_assemblies if a.id not in excluded_ids
        ]

        if not filtered_assemblies:
            self.notify(
                "All selected assemblies are excluded. Nothing to export.",
                severity="error",
            )
            return

        # Handle nested exclusions: if both parent and child are selected,
        # add child to exclusion list so it doesn't appear in parent's mesh
        for assembly in filtered_assemblies:
            child_ids = self._get_all_child_ids(assembly)
            for child_id in child_ids:
                if child_id in selected_ids:
                    # This child is separately selected, so exclude it from parent's STL
                    excluded_ids.add(child_id)

        # Export
        try:
            output_file = (
                self.step_file.parent / f"{self.step_file.stem}_converted.{format}"
            )
            exporter = get_exporter(format)
            exporter.step_file = self.step_file  # Set step file for mesh export
            exporter.excluded_assemblies = excluded_ids  # Pass exclusion list

            # Set progress callback to update progress label
            progress_label = self.query_one("#progress_label", Label)

            def progress_callback(msg: str, current: int, total: int):
                # Use call_from_thread to safely update UI from worker thread
                self.call_from_thread(progress_label.update, msg)

            exporter.progress_callback = progress_callback

            # Show initial notification
            progress_label.update(
                f"Starting export of {len(filtered_assemblies)} assemblies..."
            )

            # Run export in executor to avoid blocking UI
            import asyncio

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                exporter.export,
                filtered_assemblies,
                output_file,
                self.base_link_name,
                self.unit_scale,
            )

            unit_msg = (
                f" (units converted: {self.unit_scale}x)"
                if self.unit_scale != 1.0
                else ""
            )
            progress_label.update(f"✓ Exported to {output_file}{unit_msg}")
            self.notify("Export complete!", severity="information", timeout=5)
        except Exception as e:
            progress_label.update(f"Export failed: {str(e)}")
            self.notify(f"Export failed: {str(e)}", severity="error")

    async def _simplify_meshes(self, offset: float = 6.0):
        """Simplify meshes in the exported URDF file."""
        xacro_file = self.step_file.parent / f"{self.step_file.stem}_converted.xacro"

        if not xacro_file.exists():
            self.notify("Export URDF first before simplifying meshes", severity="error")
            return

        progress_label = self.query_one("#progress_label", Label)

        try:
            from step_to_scene.simplify import simplify_urdf_meshes

            progress_label.update(f"Simplifying meshes with offset={offset}mm...")
            self.notify(
                "Simplifying meshes... This may take a while.", severity="information"
            )

            # Run in executor to avoid blocking UI
            import asyncio

            loop = asyncio.get_event_loop()

            def progress_callback(msg: str):
                # Use call_from_thread to safely update UI from worker thread
                self.call_from_thread(progress_label.update, msg)

            def run_simplification():
                simplify_urdf_meshes(
                    urdf_path=xacro_file,
                    offset=offset,
                    update_urdf=True,
                    collision_only=True,
                    progress_callback=progress_callback,
                )

            await loop.run_in_executor(None, run_simplification)

            progress_label.update("✓ Meshes simplified successfully!")
            self.notify(
                f"Meshes simplified! Check {xacro_file.stem}_simplified.xacro",
                severity="information",
                timeout=5,
            )
        except Exception as e:
            import traceback

            error_msg = f"Simplification failed: {str(e)}"
            progress_label.update(error_msg)
            self.notify(error_msg, severity="error")
            traceback.print_exc()

    def _find_selected_children(
        self, assembly: StepAssembly, selected_ids: set[str]
    ) -> list[StepAssembly]:
        """Recursively find selected children."""
        selected = []
        for child in assembly.children:
            if child.id in selected_ids:
                selected.append(child)
            selected.extend(self._find_selected_children(child, selected_ids))
        return selected

    def action_export(self) -> None:
        """Action to show export options."""
        self.notify(
            "Choose an export format using the buttons below", severity="information"
        )

    def action_visualize(self) -> None:
        """Action to visualize URDF."""
        self.visualize_urdf()

    def action_simplify(self) -> None:
        """Action to simplify meshes."""
        self.run_worker(self._show_simplify_dialog_and_run())

    async def _show_simplify_dialog_and_run(self) -> None:
        """Show simplify dialog and run simplification."""
        result = await self.push_screen_wait(SimplifyDialog())
        if result is not None:
            await self._simplify_meshes(offset=result)

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
        """Clear all selections and exclusions."""
        self.selected_assemblies.clear()
        self.excluded_assemblies.clear()

        # Update tree labels to remove all markers
        tree = self.query_one("#assembly_tree", Tree)
        self._update_tree_labels(tree.root, set(), add_marker=False)

        self.update_selection_info()
        self.notify("Selection and exclusions cleared", severity="information")

    def action_toggle_exclude(self) -> None:
        """Toggle exclusion status of the currently highlighted node."""
        tree = self.query_one("#assembly_tree", Tree)
        if tree.cursor_node and tree.cursor_node.data:
            assembly_id = tree.cursor_node.data

            if assembly_id in self.excluded_assemblies:
                # Remove from excluded
                self.excluded_assemblies.remove(assembly_id)
                # Update label
                current_label = str(tree.cursor_node.label)
                updated_label = current_label.replace("[✗] ", "")
                # If it was also selected, add back the checkmark
                if (
                    assembly_id in self.selected_assemblies
                    and not updated_label.startswith("[✓] ")
                ):
                    updated_label = f"[✓] {updated_label}"
                tree.cursor_node.label = updated_label
                self.notify("Removed exclusion", severity="information")
            else:
                # Add to excluded
                self.excluded_assemblies.add(assembly_id)
                # Remove from selected if it was selected
                if assembly_id in self.selected_assemblies:
                    self.selected_assemblies.remove(assembly_id)
                # Update label
                current_label = str(tree.cursor_node.label)
                # Remove any existing markers first
                updated_label = current_label.replace("[✓] ", "").replace("[✗] ", "")
                updated_label = f"[✗] {updated_label}"
                tree.cursor_node.label = updated_label
                self.notify("Excluded from export", severity="warning")

            self.update_selection_info()

    def action_toggle_hide_empty(self) -> None:
        """Toggle hiding assemblies without nested parts."""
        self.hide_empty_assemblies = not self.hide_empty_assemblies
        self._rebuild_tree()

        if self.hide_empty_assemblies:
            self.notify(
                "Hiding assemblies without nested parts", severity="information"
            )
        else:
            self.notify("Showing all assemblies", severity="information")

    def action_focus_search(self) -> None:
        """Focus the search input."""
        search_input = self.query_one("#search_input", Input)
        search_input.focus()

    @on(Input.Changed, "#search_input")
    def on_search_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        self.search_query = event.value
        self._rebuild_tree()

        if self.search_query:
            self.notify(f"Filtering by: {self.search_query}", severity="information")

    def _update_tree_labels(
        self, node: TreeNode, selected_ids: set[str], add_marker: bool
    ):
        """Recursively update tree labels to show/hide selection markers."""
        for child in node.children:
            if child.data:
                current_label = str(child.label)
                if add_marker and child.data in selected_ids:
                    if not current_label.startswith("[✓] "):
                        child.label = f"[✓] {current_label}"
                elif not add_marker and current_label.startswith("[✓] "):
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
