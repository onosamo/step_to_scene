import threading
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Tree
from textual.widgets.tree import TreeNode

from step_to_scene.exporters import get_exporter
from step_to_scene.parser import StepAssembly, StepParser

SEARCH_DEBOUNCE_SECONDS = 0.2
EXPAND_ALL_RESULTS_LIMIT = 300


def fuzzy_match(query: str, text: str) -> bool:
    """True if ``query`` is a subsequence of ``text``; both must be lowercase."""
    if not query:
        return True
    iterator = iter(text)
    # `char in iterator` consumes the iterator at C speed, so this stays fast
    # even when called tens of thousands of times per query.
    return all(char in iterator for char in query)


def compute_visible_ids(
    roots: list[StepAssembly],
    query: str,
    texts_cache: dict[str, tuple[str, ...]],
) -> set[str]:
    """Ids of nodes to show for ``query``: every node whose subtree matches.

    One bottom-up pass over the tree, unlike matching each node against its
    whole subtree (which is quadratic in tree depth).
    """
    query = query.lower()
    visible: set[str] = set()

    def texts(node: StepAssembly) -> tuple[str, ...]:
        cached = texts_cache.get(node.id)
        if cached is None:
            cached = (node.name.lower(), node.description.lower(), node.id)
            texts_cache[node.id] = cached
        return cached

    def walk(node: StepAssembly) -> bool:
        hit = any(fuzzy_match(query, text) for text in texts(node))
        for child in node.children:
            if walk(child):
                hit = True
        if hit:
            visible.add(node.id)
        return hit

    for root in roots:
        walk(root)
    return visible


class SimplifyDialog(ModalScreen):
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
            yield Label("Mesh units: mm | Default: 6.0mm = 0.006m")
            with Horizontal(id="button_row"):
                yield Button("Simplify", variant="primary", id="simplify_confirm")
                yield Button("Cancel", variant="error", id="simplify_cancel")

    @on(Button.Pressed, "#simplify_confirm")
    def confirm_simplify(self) -> None:
        offset_input = self.query_one("#offset_input", Input)
        try:
            offset = float(offset_input.value)
            if offset < 0:
                self.notify("Offset must be non-negative", severity="error")
                return
            self.dismiss(offset)
        except ValueError:
            self.notify(
                "Invalid offset value. Please enter a number.", severity="error"
            )

    @on(Button.Pressed, "#simplify_cancel")
    def cancel_simplify(self) -> None:
        self.dismiss(None)


class ArchiveDialog(ModalScreen):
    CSS = """
    ArchiveDialog {
        align: center middle;
    }

    #archive_dialog {
        width: 65;
        height: 18;
        border: thick #268bd2;
        background: #fdf6e3;
        padding: 1 2;
    }

    #archive_dialog_title {
        width: 100%;
        content-align: center middle;
        text-style: bold;
        color: #268bd2;
        margin-bottom: 1;
    }

    .checkbox_row {
        width: 100%;
        height: auto;
        margin: 1 0;
    }

    #archive_button_row {
        width: 100%;
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    Button {
        margin: 0 1;
    }

    Checkbox {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="archive_dialog"):
            yield Label("Create Archive", id="archive_dialog_title")
            yield Label("Select archive options:")
            with Container(classes="checkbox_row"):
                yield Checkbox("Include STEP file", id="include_step", value=True)
            with Container(classes="checkbox_row"):
                yield Checkbox(
                    "Create simplified archive", id="create_simplified", value=True
                )
            yield Label("Archives will be saved as .tar.gz files")
            with Horizontal(id="archive_button_row"):
                yield Button("Create Archive", variant="primary", id="archive_confirm")
                yield Button("Cancel", variant="error", id="archive_cancel")

    @on(Button.Pressed, "#archive_confirm")
    def confirm_archive(self) -> None:
        include_step = self.query_one("#include_step", Checkbox).value
        create_simplified = self.query_one("#create_simplified", Checkbox).value
        self.dismiss(
            {"include_step": include_step, "create_simplified": create_simplified}
        )

    @on(Button.Pressed, "#archive_cancel")
    def cancel_archive(self) -> None:
        self.dismiss(None)


class StepExplorerApp(App):
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

    Button:disabled {
        opacity: 0.5;
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
        ("r", "archive", "Create Archive"),
        ("a", "select_all", "Select All"),
        ("c", "clear_selection", "Clear Selection"),
        ("x", "toggle_exclude", "Exclude/Include"),
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
        self.selected_assemblies: set[str] = set()
        self.excluded_assemblies: set[str] = set()
        self.search_query = ""
        self._pending_search_query = ""
        self._search_timer = None
        self._search_texts_cache: dict[str, tuple[str, ...]] = {}
        # Ids matching the current search, or None when not searching. Tree
        # nodes are created only when their parent is expanded, which keeps
        # huge assemblies responsive.
        self._visible_ids: set[str] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(f"STEP File Explorer: {self.step_file.name}", id="title")

        with Vertical(id="main_container"):
            with Container(id="search_container"):
                yield Input(
                    placeholder="Search assemblies (fuzzy match)...", id="search_input"
                )

            with Container(id="tree_container"):
                yield Tree("STEP Assemblies", id="assembly_tree")

            with Container(id="info_panel"):
                yield Label(
                    "Navigate: Up/Down | Select: Enter | Exclude: X | Export: E | Simplify: S | Visualize: V | Archive: R | Search: / | Quit: Q",
                    id="info_label",
                )
                yield Label(
                    "Selected: [+] | Excluded: [-] (strikethrough)",
                    id="help_label",
                )
                yield Label("No assemblies selected", id="selection_info")
                yield Label("", id="progress_label")

            with Horizontal(id="button_container"):
                yield Button(
                    "Export as URDF", id="export_urdf", variant="primary", disabled=True
                )
                yield Button(
                    "Simplify Meshes",
                    id="simplify_meshes",
                    variant="warning",
                    disabled=True,
                )
                yield Button(
                    "Visualize URDF",
                    id="visualize_urdf",
                    variant="success",
                    disabled=True,
                )
                yield Button(
                    "Visualize Simplified",
                    id="visualize_simplified",
                    variant="success",
                    disabled=True,
                )
                yield Button(
                    "Create Archive",
                    id="create_archive",
                    variant="default",
                    disabled=True,
                )
                yield Button("Quit", id="quit", variant="error")

        yield Footer()

    def on_mount(self) -> None:
        try:
            self.assemblies = self.parser.parse()
            self.unit_name, self.unit_scale = self.parser.get_unit_info()
            self.base_link_name = "world"
            self._rebuild_tree()
            self._update_button_states()

            if self.unit_scale != 1.0:
                self.notify(
                    f"Units detected: {self.unit_name} (will convert to meters: scale={self.unit_scale})",
                    severity="information",
                )

            self._start_geometry_preload()
        except Exception as e:
            self.exit(message=f"Error parsing STEP file: {str(e)}")

    def _start_geometry_preload(self) -> None:
        """Load CAD geometry in the background while the user browses, so
        export doesn't have to wait for it."""

        def preload():
            from step_to_scene.geometry import StepGeometry

            progress_label = self.query_one("#progress_label", Label)

            def progress(msg: str):
                self.call_from_thread(progress_label.update, msg)

            try:
                StepGeometry.for_file(self.step_file).load(progress_callback=progress)
            except Exception as e:
                self.call_from_thread(
                    progress_label.update, f"CAD geometry preload failed: {e}"
                )

        # Daemon thread instead of a textual worker so quitting the app never
        # waits for a multi-minute CAD load to finish.
        threading.Thread(target=preload, daemon=True, name="geometry-preload").start()

    def _get_xacro_file(self) -> Path:
        return self.step_file.parent / f"{self.step_file.stem}_converted.xacro"

    def _get_simplified_xacro_file(self) -> Path:
        xacro_file = self._get_xacro_file()
        return xacro_file.with_name(f"{xacro_file.stem}_simplified{xacro_file.suffix}")

    def _update_button_states(self) -> None:
        xacro_exists = self._get_xacro_file().exists()
        simplified_exists = self._get_simplified_xacro_file().exists()
        has_selection = len(self.selected_assemblies) > 0

        export_btn = self.query_one("#export_urdf", Button)
        export_btn.disabled = not has_selection

        simplify_btn = self.query_one("#simplify_meshes", Button)
        simplify_btn.disabled = not xacro_exists

        visualize_btn = self.query_one("#visualize_urdf", Button)
        visualize_btn.disabled = not xacro_exists

        visualize_simplified_btn = self.query_one("#visualize_simplified", Button)
        visualize_simplified_btn.disabled = not simplified_exists

        archive_btn = self.query_one("#create_archive", Button)
        archive_btn.disabled = not xacro_exists

    def _rebuild_tree(self) -> int:
        """Rebuild the tree for the current query; returns the match count."""
        tree = self.query_one("#assembly_tree", Tree)
        tree.clear()
        tree.root.expand()

        if not self.search_query:
            self._visible_ids = None
            for assembly in self.assemblies:
                self._add_tree_node(tree.root, assembly)
            return 0

        visible = compute_visible_ids(
            self.assemblies, self.search_query, self._search_texts_cache
        )
        self._visible_ids = visible

        if len(visible) <= EXPAND_ALL_RESULTS_LIMIT:
            # Few matches: materialize and expand everything.
            for assembly in self.assemblies:
                if assembly.id in visible:
                    self._add_tree_node(tree.root, assembly, recursive=True)
            if visible:
                tree.root.expand_all()
        else:
            # Broad query (e.g. one letter): stay lazy so the UI never has to
            # create tens of thousands of tree nodes at once.
            for assembly in self.assemblies:
                if assembly.id in visible:
                    self._add_tree_node(tree.root, assembly)
        return len(visible)

    def _visible_children(self, assembly: StepAssembly) -> list[StepAssembly]:
        if self._visible_ids is None:
            return assembly.children
        return [c for c in assembly.children if c.id in self._visible_ids]

    def _add_tree_node(
        self, parent_node: TreeNode, assembly: StepAssembly, recursive: bool = False
    ) -> None:
        """Add a node; children are added now or lazily on expansion."""
        label = self._format_assembly_label(assembly)
        children = self._visible_children(assembly)
        if not children:
            parent_node.add_leaf(label, data=assembly.id)
            return
        node = parent_node.add(label, data=assembly.id)
        if recursive:
            for child in children:
                self._add_tree_node(node, child, recursive=True)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        if node.data is None or node.children:
            return
        assembly = self._get_assembly_by_id(node.data)
        if assembly is None:
            return
        for child in self._visible_children(assembly):
            self._add_tree_node(node, child)

    def _format_assembly_label(self, assembly: StepAssembly) -> str:
        name = assembly.name
        if assembly.product_id_field and assembly.product_id_field != assembly.name:
            name = f"{assembly.name} [{assembly.product_id_field}]"
        if assembly.description:
            base_label = f"{name} - {assembly.description} (ID: {assembly.id})"
        else:
            base_label = f"{name} (ID: {assembly.id})"

        if assembly.id in self.excluded_assemblies:
            return f"[-] [strike]{base_label}[/strike]"
        elif assembly.id in self.selected_assemblies:
            return f"[+] {base_label}"

        return base_label

    def update_selection_info(self):
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

        self._update_button_states()

    @on(Button.Pressed, "#export_urdf")
    async def export_urdf(self) -> None:
        await self._export("urdf")

    @on(Button.Pressed, "#visualize_urdf")
    def visualize_urdf(self) -> None:
        xacro_file = self._get_xacro_file()
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
        simplified_xacro = self._get_simplified_xacro_file()

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
        self.run_worker(self._show_simplify_dialog_and_run())

    @on(Button.Pressed, "#create_archive")
    def create_archive_button(self) -> None:
        self.run_worker(self._show_archive_dialog_and_run())

    @on(Button.Pressed, "#quit")
    def quit_app(self) -> None:
        self.exit()

    async def _export(self, format: str):
        selected_ids = self.selected_assemblies
        excluded_ids = self.excluded_assemblies.copy()

        if not selected_ids:
            self.notify(
                "No assemblies selected. Please select assemblies first.",
                severity="error",
            )
            return

        assemblies_by_id = {}
        for assembly in self.assemblies:
            if assembly.id in selected_ids:
                assemblies_by_id[assembly.id] = assembly
            for child in self._find_selected_children(assembly, selected_ids):
                assemblies_by_id[child.id] = child

        selected_assemblies = list(assemblies_by_id.values())

        if not selected_assemblies:
            self.notify("No assemblies to export.", severity="error")
            return

        filtered_assemblies = [
            a for a in selected_assemblies if a.id not in excluded_ids
        ]

        if not filtered_assemblies:
            self.notify(
                "All selected assemblies are excluded. Nothing to export.",
                severity="error",
            )
            return

        for assembly in filtered_assemblies:
            child_ids = self._get_all_child_ids(assembly)
            for child_id in child_ids:
                if child_id in selected_ids:
                    excluded_ids.add(child_id)

        progress_label = self.query_one("#progress_label", Label)

        try:
            output_file = (
                self.step_file.parent / f"{self.step_file.stem}_converted.{format}"
            )
            exporter = get_exporter(format)
            exporter.step_file = self.step_file
            exporter.excluded_assemblies = excluded_ids

            def progress_callback(msg: str):
                self.call_from_thread(progress_label.update, msg)

            exporter.progress_callback = progress_callback

            progress_label.update(
                f"Starting export of {len(filtered_assemblies)} assemblies..."
            )

            import asyncio

            loop = asyncio.get_event_loop()
            report = await loop.run_in_executor(
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
            progress_label.update(
                f"Exported to {output_file}{unit_msg} - {report.summary()}"
            )
            if report.failures:
                failed_names = ", ".join(e.name for e in report.failures[:5])
                if len(report.failures) > 5:
                    failed_names += ", ..."
                self.notify(
                    f"Export finished with {len(report.failures)} failed meshes: "
                    f"{failed_names}. See the export report next to the XACRO file.",
                    severity="warning",
                    timeout=15,
                )
            else:
                self.notify("Export complete!", severity="information", timeout=5)
            self._update_button_states()
        except Exception as e:
            progress_label.update(f"Export failed: {str(e)}")
            self.notify(f"Export failed: {str(e)}", severity="error")

    async def _simplify_meshes(self, offset: float = 6.0):
        xacro_file = self._get_xacro_file()

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

            import asyncio

            loop = asyncio.get_event_loop()

            def progress_callback(msg: str):
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

            progress_label.update("Meshes simplified successfully!")
            self.notify(
                f"Meshes simplified! Check {xacro_file.stem}_simplified.xacro",
                severity="information",
                timeout=5,
            )
            self._update_button_states()
        except Exception as e:
            import traceback

            error_msg = f"Simplification failed: {str(e)}"
            progress_label.update(error_msg)
            self.notify(error_msg, severity="error")
            traceback.print_exc()

    async def _create_archive(
        self, include_step: bool = True, create_simplified: bool = True
    ):
        xacro_file = self._get_xacro_file()

        if not xacro_file.exists():
            self.notify("Export URDF first before creating archive", severity="error")
            return

        progress_label = self.query_one("#progress_label", Label)

        try:
            from step_to_scene.archiver import archive_assembly

            progress_label.update("Creating archive...")
            self.notify(
                "Creating archive... This may take a moment.", severity="information"
            )

            import asyncio

            loop = asyncio.get_event_loop()

            def progress_callback(msg: str):
                self.call_from_thread(progress_label.update, msg)

            def run_archive():
                return archive_assembly(
                    main_file=xacro_file,
                    output_dir=None,
                    include_step=include_step,
                    create_simplified=create_simplified,
                    progress_callback=progress_callback,
                )

            original, simplified = await loop.run_in_executor(None, run_archive)

            if simplified:
                progress_label.update(
                    f"Archives created: {original.name}, {simplified.name}"
                )
                self.notify(
                    "Archives created successfully!",
                    severity="information",
                    timeout=5,
                )
            else:
                progress_label.update(f"Archive created: {original.name}")
                self.notify(
                    f"Archive created: {original.name}",
                    severity="information",
                    timeout=5,
                )
        except Exception as e:
            import traceback

            error_msg = f"Archive creation failed: {str(e)}"
            progress_label.update(error_msg)
            self.notify(error_msg, severity="error")
            traceback.print_exc()

    def _find_selected_children(
        self, assembly: StepAssembly, selected_ids: set[str]
    ) -> list[StepAssembly]:
        selected = []
        for child in assembly.children:
            if child.id in selected_ids:
                selected.append(child)
            selected.extend(self._find_selected_children(child, selected_ids))
        return selected

    def action_export(self) -> None:
        if not self.selected_assemblies:
            self.notify("Select assemblies first before exporting", severity="warning")
        else:
            self.run_worker(self._export("urdf"))

    def action_visualize(self) -> None:
        self.visualize_urdf()

    def action_simplify(self) -> None:
        if not self._get_xacro_file().exists():
            self.notify("Export URDF first before simplifying", severity="warning")
            return
        self.run_worker(self._show_simplify_dialog_and_run())

    def action_archive(self) -> None:
        if not self._get_xacro_file().exists():
            self.notify("Export URDF first before creating archive", severity="warning")
            return
        self.run_worker(self._show_archive_dialog_and_run())

    async def _show_simplify_dialog_and_run(self) -> None:
        result = await self.push_screen_wait(SimplifyDialog())
        if result is not None:
            await self._simplify_meshes(offset=result)

    async def _show_archive_dialog_and_run(self) -> None:
        result = await self.push_screen_wait(ArchiveDialog())
        if result is not None:
            await self._create_archive(
                include_step=result["include_step"],
                create_simplified=result["create_simplified"],
            )

    def action_select_all(self) -> None:
        all_ids: set[str] = set()
        for assembly in self.assemblies:
            all_ids.add(assembly.id)
            all_ids.update(self._get_all_child_ids(assembly))

        self.selected_assemblies = all_ids - self.excluded_assemblies

        tree = self.query_one("#assembly_tree", Tree)
        self._update_all_tree_labels(tree.root)

        self.update_selection_info()
        self.notify("All assemblies selected", severity="information")

    def action_clear_selection(self) -> None:
        self.selected_assemblies.clear()
        self.excluded_assemblies.clear()

        tree = self.query_one("#assembly_tree", Tree)
        self._update_all_tree_labels(tree.root)

        self.update_selection_info()
        self.notify("Selection and exclusions cleared", severity="information")

    def action_toggle_exclude(self) -> None:
        tree = self.query_one("#assembly_tree", Tree)
        if tree.cursor_node and tree.cursor_node.data:
            assembly_id = tree.cursor_node.data

            if assembly_id in self.excluded_assemblies:
                self.excluded_assemblies.remove(assembly_id)
                self.notify("Removed exclusion", severity="information")
            else:
                self.excluded_assemblies.add(assembly_id)
                if assembly_id in self.selected_assemblies:
                    self.selected_assemblies.remove(assembly_id)
                self.notify("Excluded from export", severity="warning")

            self._update_node_label(tree.cursor_node)
            self.update_selection_info()

    def action_focus_search(self) -> None:
        search_input = self.query_one("#search_input", Input)
        search_input.focus()

    @on(Input.Changed, "#search_input")
    def on_search_changed(self, event: Input.Changed) -> None:
        # Debounce: rebuilding the tree on every keystroke makes typing lag
        # on large assemblies.
        self._pending_search_query = event.value
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(SEARCH_DEBOUNCE_SECONDS, self._apply_search)

    def _apply_search(self) -> None:
        self._search_timer = None
        if self._pending_search_query == self.search_query:
            return
        self.search_query = self._pending_search_query
        match_count = self._rebuild_tree()

        progress_label = self.query_one("#progress_label", Label)
        if self.search_query:
            progress_label.update(
                f"Filtering by: {self.search_query} ({match_count} matches)"
            )
        else:
            progress_label.update("")

    def _get_assembly_by_id(self, assembly_id: str) -> StepAssembly | None:
        return self.parser.get_assembly(assembly_id)

    def _update_node_label(self, node: TreeNode) -> None:
        if node.data:
            assembly = self._get_assembly_by_id(node.data)
            if assembly:
                node.label = self._format_assembly_label(assembly)

    def _update_all_tree_labels(self, node: TreeNode) -> None:
        for child in node.children:
            self._update_node_label(child)
            self._update_all_tree_labels(child)

    def _get_all_child_ids(self, assembly: StepAssembly) -> set[str]:
        ids: set[str] = set()
        for child in assembly.children:
            ids.add(child.id)
            ids.update(self._get_all_child_ids(child))
        return ids

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data:
            assembly_id = event.node.data

            if assembly_id in self.excluded_assemblies:
                self.notify(
                    "This assembly is excluded. Press X to remove exclusion first.",
                    severity="warning",
                )
                return

            if assembly_id in self.selected_assemblies:
                self.selected_assemblies.remove(assembly_id)
            else:
                self.selected_assemblies.add(assembly_id)

            self._update_node_label(event.node)

        self.update_selection_info()


def run_explorer(step_file: Path) -> None:
    app = StepExplorerApp(step_file)
    app.run()
