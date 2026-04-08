"""Desktop GUI for step_to_scene using PySide6 (Qt)."""

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from step_to_scene.exporters import get_exporter
from step_to_scene.parser import StepAssembly, StepParser


# ---------------------------------------------------------------------------
# Worker threads for long-running operations
# ---------------------------------------------------------------------------

class ExportWorker(QThread):
    progress = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        assemblies: list[StepAssembly],
        output_path: Path,
        base_link: str,
        unit_scale: float,
        step_file: Path,
        excluded_ids: set[str],
        fmt: str = "urdf",
    ):
        super().__init__()
        self.assemblies = assemblies
        self.output_path = output_path
        self.base_link = base_link
        self.unit_scale = unit_scale
        self.step_file = step_file
        self.excluded_ids = excluded_ids
        self.fmt = fmt

    def run(self):
        try:
            exporter = get_exporter(self.fmt)
            exporter.step_file = self.step_file
            exporter.excluded_assemblies = self.excluded_ids

            def cb(msg, _cur, _tot):
                self.progress.emit(msg)

            exporter.progress_callback = cb
            exporter.export(
                self.assemblies,
                self.output_path,
                self.base_link,
                self.unit_scale,
            )
            self.finished.emit(str(self.output_path))
        except Exception as e:
            self.error.emit(str(e))


class SimplifyWorker(QThread):
    progress = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, xacro_path: Path, offset: float):
        super().__init__()
        self.xacro_path = xacro_path
        self.offset = offset

    def run(self):
        try:
            from step_to_scene.simplify import simplify_urdf_meshes

            def cb(msg):
                self.progress.emit(msg)

            simplify_urdf_meshes(
                urdf_path=self.xacro_path,
                offset=self.offset,
                update_urdf=True,
                collision_only=True,
                progress_callback=cb,
            )
            self.finished.emit("Meshes simplified successfully!")
        except Exception as e:
            self.error.emit(str(e))


class ArchiveWorker(QThread):
    progress = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        xacro_path: Path,
        include_step: bool,
        create_simplified: bool,
    ):
        super().__init__()
        self.xacro_path = xacro_path
        self.include_step = include_step
        self.create_simplified = create_simplified

    def run(self):
        try:
            from step_to_scene.archiver import archive_assembly

            def cb(msg):
                self.progress.emit(msg)

            original, simplified = archive_assembly(
                main_file=self.xacro_path,
                output_dir=None,
                include_step=self.include_step,
                create_simplified=self.create_simplified,
                progress_callback=cb,
            )
            if simplified:
                self.finished.emit(
                    f"Archives created: {original.name}, {simplified.name}"
                )
            else:
                self.finished.emit(f"Archive created: {original.name}")
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

class SimplifyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Simplify Meshes")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Offset distance (mm):"))
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(0.0, 1000.0)
        self.offset_spin.setValue(6.0)
        self.offset_spin.setDecimals(2)
        layout.addWidget(self.offset_spin)

        layout.addWidget(QLabel("Adds clearance around collision meshes."))
        layout.addWidget(QLabel("Mesh units: mm | Default: 6.0 mm = 0.006 m"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_offset(self) -> float:
        return self.offset_spin.value()


class ArchiveDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Archive")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select archive options:"))

        self.include_step_cb = QCheckBox("Include STEP file")
        self.include_step_cb.setChecked(True)
        layout.addWidget(self.include_step_cb)

        self.create_simplified_cb = QCheckBox("Create simplified archive")
        self.create_simplified_cb.setChecked(True)
        layout.addWidget(self.create_simplified_cb)

        layout.addWidget(QLabel("Archives will be saved as .tar.gz files"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_options(self) -> dict:
        return {
            "include_step": self.include_step_cb.isChecked(),
            "create_simplified": self.create_simplified_cb.isChecked(),
        }


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class StepExplorerWindow(QMainWindow):
    def __init__(self, step_file: Optional[Path] = None):
        super().__init__()
        self.setWindowTitle("STEP to Scene Explorer")
        self.resize(1000, 700)

        self.step_file: Optional[Path] = None
        self.parser: Optional[StepParser] = None
        self.assemblies: list[StepAssembly] = []
        self.unit_scale = 1.0
        self.unit_name = "UNKNOWN"
        self.base_link_name = "world"
        self.selected_ids: set[str] = set()
        self.excluded_ids: set[str] = set()
        self.export_dir: Optional[Path] = None

        self._worker: Optional[QThread] = None

        self._build_ui()
        self._connect_signals()

        if step_file:
            self._load_step_file(step_file)

    # ---- UI construction --------------------------------------------------

    def _build_ui(self):
        # Menu bar
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        open_action = QAction("&Open STEP File…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_file_dialog)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # File info bar
        file_bar = QHBoxLayout()
        self.file_label = QLabel("No file loaded")
        self.file_label.setStyleSheet("font-weight: bold;")
        file_bar.addWidget(self.file_label)
        file_bar.addStretch()
        self.unit_label = QLabel("")
        file_bar.addWidget(self.unit_label)
        main_layout.addLayout(file_bar)

        # Splitter: tree | info
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: tree + search
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search assemblies (fuzzy match)…")
        left_layout.addWidget(self.search_input)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Assembly", "ID"])
        self.tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.tree.setSelectionMode(
            QTreeWidget.SelectionMode.ExtendedSelection
        )
        left_layout.addWidget(self.tree)

        # Selection buttons
        sel_row = QHBoxLayout()
        self.btn_select_all = QPushButton("Select All")
        self.btn_clear = QPushButton("Clear Selection")
        self.btn_toggle_exclude = QPushButton("Toggle Exclude")
        sel_row.addWidget(self.btn_select_all)
        sel_row.addWidget(self.btn_clear)
        sel_row.addWidget(self.btn_toggle_exclude)
        left_layout.addLayout(sel_row)

        splitter.addWidget(left)

        # Right: info panel
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        info_group = QGroupBox("Info")
        info_layout = QVBoxLayout(info_group)
        self.selection_label = QLabel("No assemblies selected")
        info_layout.addWidget(self.selection_label)
        right_layout.addWidget(info_group)

        # Export directory
        dir_group = QGroupBox("Export Directory")
        dir_layout = QHBoxLayout(dir_group)
        self.dir_input = QLineEdit()
        self.dir_input.setPlaceholderText("Same as STEP file (default)")
        self.dir_input.setReadOnly(True)
        dir_layout.addWidget(self.dir_input)
        self.btn_browse_dir = QPushButton("Browse…")
        self.btn_browse_dir.setFixedWidth(80)
        dir_layout.addWidget(self.btn_browse_dir)
        right_layout.addWidget(dir_group)

        # Action buttons
        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_group)

        self.btn_export = QPushButton("Export as URDF")
        self.btn_export.setEnabled(False)
        actions_layout.addWidget(self.btn_export)

        self.btn_simplify = QPushButton("Simplify Meshes")
        self.btn_simplify.setEnabled(False)
        actions_layout.addWidget(self.btn_simplify)

        self.btn_visualize = QPushButton("Visualize URDF")
        self.btn_visualize.setEnabled(False)
        actions_layout.addWidget(self.btn_visualize)

        self.btn_visualize_simplified = QPushButton("Visualize Simplified")
        self.btn_visualize_simplified.setEnabled(False)
        actions_layout.addWidget(self.btn_visualize_simplified)

        self.btn_archive = QPushButton("Create Archive")
        self.btn_archive.setEnabled(False)
        actions_layout.addWidget(self.btn_archive)

        right_layout.addWidget(actions_group)
        right_layout.addStretch()
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        main_layout.addWidget(self.progress_label)

        # Status bar
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

    def _connect_signals(self):
        self.search_input.textChanged.connect(self._on_search_changed)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_clear.clicked.connect(self._clear_selection)
        self.btn_toggle_exclude.clicked.connect(self._toggle_exclude)
        self.btn_export.clicked.connect(self._export)
        self.btn_browse_dir.clicked.connect(self._browse_export_dir)
        self.btn_simplify.clicked.connect(self._simplify)
        self.btn_visualize.clicked.connect(self._visualize)
        self.btn_visualize_simplified.clicked.connect(self._visualize_simplified)
        self.btn_archive.clicked.connect(self._archive)

    # ---- File loading -----------------------------------------------------

    def _open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open STEP File", "", "STEP Files (*.step *.stp);;All Files (*)"
        )
        if path:
            self._load_step_file(Path(path))

    def _browse_export_dir(self):
        start = str(self.export_dir or (self.step_file.parent if self.step_file else ""))
        directory = QFileDialog.getExistingDirectory(
            self, "Select Export Directory", start
        )
        if directory:
            self.export_dir = Path(directory)
            self.dir_input.setText(str(self.export_dir))
            self._update_buttons()

    def _load_step_file(self, path: Path):
        self.step_file = path
        self.file_label.setText(f"File: {path.name}")
        self.statusBar().showMessage(f"Loading {path.name}…")
        QApplication.processEvents()

        try:
            self.parser = StepParser(path)
            self.assemblies = self.parser.parse()
            self.unit_name, self.unit_scale = self.parser.get_unit_info()
            self.base_link_name = "world"
            self.unit_label.setText(
                f"Units: {self.unit_name} (scale → m: {self.unit_scale})"
            )
            self.selected_ids.clear()
            self.excluded_ids.clear()
            self._rebuild_tree()
            self._update_buttons()
            self.statusBar().showMessage(
                f"Loaded {len(self.assemblies)} top-level assemblies", 5000
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load STEP file:\n{e}")
            self.statusBar().showMessage("Load failed")

    # ---- Tree management --------------------------------------------------

    def _rebuild_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        query = self.search_input.text()
        for asm in self.assemblies:
            self._add_assembly_item(self.tree.invisibleRootItem(), asm, query)
        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _fuzzy_match(self, query: str, text: str) -> bool:
        if not query:
            return True
        qi = 0
        for ch in text.lower():
            if qi < len(query) and ch == query[qi]:
                qi += 1
        return qi == len(query)

    def _assembly_matches(self, asm: StepAssembly, query: str) -> bool:
        if not query:
            return True
        q = query.lower()
        if self._fuzzy_match(q, asm.name):
            return True
        if asm.description and self._fuzzy_match(q, asm.description):
            return True
        if self._fuzzy_match(q, str(asm.id)):
            return True
        return any(self._assembly_matches(c, query) for c in asm.children)

    def _add_assembly_item(
        self, parent_item: QTreeWidgetItem, asm: StepAssembly, query: str
    ):
        if not self._assembly_matches(asm, query):
            return

        label = asm.name
        if asm.description:
            label += f" — {asm.description}"

        item = QTreeWidgetItem(parent_item, [label, str(asm.id)])
        item.setData(0, Qt.ItemDataRole.UserRole, asm.id)
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsAutoTristate
        )

        if asm.id in self.excluded_ids:
            item.setCheckState(0, Qt.CheckState.Unchecked)
            font = item.font(0)
            font.setStrikeOut(True)
            item.setFont(0, font)
        elif asm.id in self.selected_ids:
            item.setCheckState(0, Qt.CheckState.Checked)
        else:
            item.setCheckState(0, Qt.CheckState.Unchecked)

        for child in asm.children:
            self._add_assembly_item(item, child, query)

    def _on_search_changed(self, text: str):
        self._rebuild_tree()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        if column != 0:
            return
        asm_id = item.data(0, Qt.ItemDataRole.UserRole)
        if asm_id is None:
            return

        if asm_id in self.excluded_ids:
            # don't allow selecting excluded items via checkbox
            self.tree.blockSignals(True)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            self.tree.blockSignals(False)
            return

        checked = item.checkState(0) == Qt.CheckState.Checked
        if checked:
            self.selected_ids.add(asm_id)
        else:
            self.selected_ids.discard(asm_id)

        self._update_selection_info()
        self._update_buttons()

    # ---- Selection helpers ------------------------------------------------

    def _select_all(self):
        self.tree.blockSignals(True)
        self._set_all_check_states(
            self.tree.invisibleRootItem(), Qt.CheckState.Checked
        )
        self.tree.blockSignals(False)
        self._collect_all_ids(self.tree.invisibleRootItem(), self.selected_ids)
        self.selected_ids -= self.excluded_ids
        self._update_selection_info()
        self._update_buttons()

    def _clear_selection(self):
        self.selected_ids.clear()
        self.excluded_ids.clear()
        self._rebuild_tree()
        self._update_selection_info()
        self._update_buttons()

    def _toggle_exclude(self):
        items = self.tree.selectedItems()
        if not items:
            self.statusBar().showMessage("Select items in the tree first", 3000)
            return
        self.tree.blockSignals(True)
        for item in items:
            asm_id = item.data(0, Qt.ItemDataRole.UserRole)
            if asm_id is None:
                continue
            if asm_id in self.excluded_ids:
                self.excluded_ids.discard(asm_id)
                font = item.font(0)
                font.setStrikeOut(False)
                item.setFont(0, font)
            else:
                self.excluded_ids.add(asm_id)
                self.selected_ids.discard(asm_id)
                item.setCheckState(0, Qt.CheckState.Unchecked)
                font = item.font(0)
                font.setStrikeOut(True)
                item.setFont(0, font)
        self.tree.blockSignals(False)
        self._update_selection_info()
        self._update_buttons()

    def _set_all_check_states(self, parent: QTreeWidgetItem, state: Qt.CheckState):
        for i in range(parent.childCount()):
            child = parent.child(i)
            asm_id = child.data(0, Qt.ItemDataRole.UserRole)
            if asm_id not in self.excluded_ids:
                child.setCheckState(0, state)
            self._set_all_check_states(child, state)

    def _collect_all_ids(self, parent: QTreeWidgetItem, ids: set[str]):
        for i in range(parent.childCount()):
            child = parent.child(i)
            asm_id = child.data(0, Qt.ItemDataRole.UserRole)
            if asm_id:
                ids.add(asm_id)
            self._collect_all_ids(child, ids)

    # ---- State helpers ----------------------------------------------------

    def _update_selection_info(self):
        n_sel = len(self.selected_ids)
        n_exc = len(self.excluded_ids)
        parts = []
        if n_sel:
            parts.append(f"{n_sel} selected")
        if n_exc:
            parts.append(f"{n_exc} excluded")
        self.selection_label.setText(", ".join(parts) if parts else "No assemblies selected")

    def _get_export_dir(self) -> Path:
        if self.export_dir is not None:
            return self.export_dir
        return self.step_file.parent

    def _get_xacro_file(self) -> Path:
        return self._get_export_dir() / f"{self.step_file.stem}_converted.xacro"

    def _get_simplified_xacro_file(self) -> Path:
        xf = self._get_xacro_file()
        return xf.with_name(f"{xf.stem}_simplified{xf.suffix}")

    def _update_buttons(self):
        has_file = self.step_file is not None
        has_sel = len(self.selected_ids) > 0
        xacro_exists = has_file and self._get_xacro_file().exists()
        simplified_exists = has_file and self._get_simplified_xacro_file().exists()

        self.btn_export.setEnabled(has_sel)
        self.btn_simplify.setEnabled(xacro_exists)
        self.btn_visualize.setEnabled(xacro_exists)
        self.btn_visualize_simplified.setEnabled(simplified_exists)
        self.btn_archive.setEnabled(xacro_exists)

    # ---- Actions ----------------------------------------------------------

    def _get_selected_assemblies(self) -> list[StepAssembly]:
        by_id: dict[str, StepAssembly] = {}

        def collect(asm_list: list[StepAssembly]):
            for asm in asm_list:
                if asm.id in self.selected_ids:
                    by_id[asm.id] = asm
                collect(asm.children)

        collect(self.assemblies)
        return [a for a in by_id.values() if a.id not in self.excluded_ids]

    def _get_all_child_ids(self, asm: StepAssembly) -> set[str]:
        ids: set[str] = set()
        for c in asm.children:
            ids.add(c.id)
            ids.update(self._get_all_child_ids(c))
        return ids

    def _set_busy(self, busy: bool, msg: str = ""):
        self.progress_bar.setVisible(busy)
        self.progress_label.setText(msg)
        self.btn_export.setEnabled(not busy and len(self.selected_ids) > 0)
        self.btn_simplify.setEnabled(not busy)
        self.btn_archive.setEnabled(not busy)

    def _export(self):
        selected = self._get_selected_assemblies()
        if not selected:
            QMessageBox.warning(self, "Nothing to export", "Select assemblies first.")
            return

        # Build effective excluded IDs (avoid exporting nested children)
        excluded = self.excluded_ids.copy()
        for asm in selected:
            for cid in self._get_all_child_ids(asm):
                if cid in self.selected_ids:
                    excluded.add(cid)

        output = self._get_export_dir() / f"{self.step_file.stem}_converted.urdf"
        self._set_busy(True, f"Exporting {len(selected)} assemblies…")

        self._worker = ExportWorker(
            assemblies=selected,
            output_path=output,
            base_link=self.base_link_name,
            unit_scale=self.unit_scale,
            step_file=self.step_file,
            excluded_ids=excluded,
        )
        self._worker.progress.connect(lambda m: self.progress_label.setText(m))
        self._worker.finished.connect(self._on_export_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_export_done(self, path: str):
        self._set_busy(False, f"Exported to {path}")
        self._update_buttons()
        self.statusBar().showMessage("Export complete!", 5000)

    def _simplify(self):
        dlg = SimplifyDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        offset = dlg.get_offset()
        xacro = self._get_xacro_file()
        if not xacro.exists():
            QMessageBox.warning(self, "Error", "Export URDF first.")
            return

        self._set_busy(True, f"Simplifying meshes (offset={offset} mm)…")
        self._worker = SimplifyWorker(xacro, offset)
        self._worker.progress.connect(lambda m: self.progress_label.setText(m))
        self._worker.finished.connect(self._on_simplify_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_simplify_done(self, msg: str):
        self._set_busy(False, msg)
        self._update_buttons()
        self.statusBar().showMessage(msg, 5000)

    def _visualize(self):
        xacro = self._get_xacro_file()
        if not xacro.exists():
            QMessageBox.warning(self, "Error", "Export URDF first.")
            return
        try:
            from step_to_scene.visualizer import visualize_urdf
            visualize_urdf(xacro)
        except Exception as e:
            QMessageBox.critical(self, "Visualization Error", str(e))

    def _visualize_simplified(self):
        xacro = self._get_simplified_xacro_file()
        if not xacro.exists():
            QMessageBox.warning(self, "Error", "Simplify meshes first.")
            return
        try:
            from step_to_scene.visualizer import visualize_urdf
            visualize_urdf(xacro)
        except Exception as e:
            QMessageBox.critical(self, "Visualization Error", str(e))

    def _archive(self):
        dlg = ArchiveDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.get_options()
        xacro = self._get_xacro_file()
        if not xacro.exists():
            QMessageBox.warning(self, "Error", "Export URDF first.")
            return

        self._set_busy(True, "Creating archive…")
        self._worker = ArchiveWorker(
            xacro, opts["include_step"], opts["create_simplified"]
        )
        self._worker.progress.connect(lambda m: self.progress_label.setText(m))
        self._worker.finished.connect(self._on_archive_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_archive_done(self, msg: str):
        self._set_busy(False, msg)
        self.statusBar().showMessage(msg, 5000)

    def _on_worker_error(self, msg: str):
        self._set_busy(False, f"Error: {msg}")
        QMessageBox.critical(self, "Error", msg)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_gui(step_file: Optional[Path] = None):
    app = QApplication.instance() or QApplication(sys.argv)
    window = StepExplorerWindow(step_file)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="STEP to Scene GUI")
    p.add_argument("step_file", nargs="?", default=None, help="Path to STEP file")
    args = p.parse_args()
    run_gui(Path(args.step_file) if args.step_file else None)
