# fieldstool/gui/main_window.py
"""
MainWindow — root-sheet picker, ComponentTreeDock (parsed from the
SCHEMATIC, see schema_model.py), an edit panel, PendingChangesDock, and
Apply. Two phases with different KiCad requirements (see
fieldstool/__init__.py):

1. Staging (KiCad open): a live, read-only BoardConnection (this
   module's own fieldstool/gui/connection.py, NOT kicadstamp/gui's —
   separate process) watches the PCB selection on a fast QTimer, same
   pattern as kicadstamp/gui/main_window.py's SELECTION_POLL — because
   PCB/schematic selection cross-probe in KiCad, a selection made in
   Eeschema shows up here too. The tree also lets you pick a target
   directly (leaf click = one ref, group click = a whole Role/Cluster
   group) without needing a live selection at all. Either way, staging
   only ever writes to PendingRegistry (JSON) — nothing touches
   .kicad_sch yet.
2. Apply (KiCad must be closed): converts the whole pending queue into
   real edits via fieldstool.set_fields.plan_set_edits_for_root() +
   fieldstool.editing.write_files() — the same offline pipeline the CLI's
   `set` subcommand uses. Gated by check_kicad_not_running() — if KiCad
   is running, this is an INSTRUCTION dialog, never automated (confirmed
   2026-08-01: kipy has no app-level quit/save-all call to automate this
   safely, see handoff_2026_08_01_fieldstool.md).
"""
import logging
from pathlib import Path
from typing import List, Optional

from kipy.board_types import FootprintInstance
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                              QLabel, QMainWindow, QMessageBox,
                              QPushButton, QVBoxLayout, QWidget)

from kicadstamp.i18n import _

from .. import editing, set_fields
from ..exceptions import FieldsToolError
from ..schema_model import load_schematic_components
from . import settings
from .connection import BoardConnection
from .pending import PendingChangesDock, PendingRegistry
from .tree import ComponentTreeDock

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 2000
SELECTION_POLL_INTERVAL_MS = 400


class MainWindow(QMainWindow):
    def __init__(self, timeout_ms: int):
        super().__init__()
        self.setWindowTitle(_("fieldstool"))
        self.resize(420, 700)

        self.connection = BoardConnection(timeout_ms=timeout_ms)
        self._root_sheet: Optional[Path] = None
        self._current_targets: List[str] = []

        central = QWidget()
        layout = QVBoxLayout(central)

        root_row = QHBoxLayout()
        self.root_label = QLabel(_("No root sheet picked"))
        self.root_label.setWordWrap(True)
        root_row.addWidget(self.root_label, 1)
        pick_button = QPushButton(_("Pick root sheet..."))
        pick_button.clicked.connect(self._on_pick_root_sheet)
        root_row.addWidget(pick_button)
        rescan_button = QPushButton(_("Rescan"))
        rescan_button.clicked.connect(self._rescan)
        root_row.addWidget(rescan_button)
        layout.addLayout(root_row)

        self.status_label = QLabel(_("Not connected"))
        layout.addWidget(self.status_label)

        self.tree_dock = ComponentTreeDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)
        self.tree_dock.on_leaf_picked = self._set_targets
        self.tree_dock.on_group_picked = self._on_group_picked

        edit_box = QWidget()
        edit_layout = QVBoxLayout(edit_box)
        self.target_label = QLabel(_("Nothing selected"))
        self.target_label.setWordWrap(True)
        edit_layout.addWidget(self.target_label)
        form = QFormLayout()
        self.role_combo = QComboBox()
        self.role_combo.setEditable(True)
        form.addRow(_("Role:"), self.role_combo)
        self.cluster_combo = QComboBox()
        self.cluster_combo.setEditable(True)
        form.addRow(_("Cluster:"), self.cluster_combo)
        edit_layout.addLayout(form)
        self.stage_button = QPushButton(_("Stage"))
        self.stage_button.setEnabled(False)
        self.stage_button.clicked.connect(self._on_stage)
        edit_layout.addWidget(self.stage_button)
        layout.addWidget(edit_box)

        self._pending_registry = PendingRegistry(Path(settings.SETTINGS_PATH).parent / "no_project.pending.json")
        self.pending_dock = PendingChangesDock(self, self._pending_registry)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.pending_dock)
        self.pending_dock.on_apply_clicked = self._on_apply

        self.setCentralWidget(central)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_INTERVAL_MS)

        self._selection_timer = QTimer(self)
        self._selection_timer.timeout.connect(self._poll_selection)
        self._selection_timer.start(SELECTION_POLL_INTERVAL_MS)

        self._restore_last_root_sheet()

    # ── Root sheet ───────────────────────────────────────────────────────

    def _restore_last_root_sheet(self) -> None:
        saved = settings.load().get("root_sheet")
        if saved and Path(saved).is_file():
            self._set_root_sheet(Path(saved))

    def _on_pick_root_sheet(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, _("Pick the project's root .kicad_sch"), "", "KiCad Schematic (*.kicad_sch)")
        if path:
            self._set_root_sheet(Path(path))

    def _set_root_sheet(self, path: Path) -> None:
        self._root_sheet = path
        self.root_label.setText(str(path))
        data = settings.load()
        data["root_sheet"] = str(path)
        settings.save(data)

        pending_path = path.with_suffix(".pending.json")
        self._pending_registry = PendingRegistry(pending_path)
        self.pending_dock.registry = self._pending_registry
        self.pending_dock.refresh()

        self._rescan()

    def _rescan(self) -> None:
        """Explicit action, not auto-polled — the schematic only changes
        when someone saves in Eeschema, not every 2 seconds (same
        "explicit refresh, no silent polling" discipline
        kicadstamp/gui/main_window.py documents for its own tree)."""
        if self._root_sheet is None:
            return
        try:
            components = load_schematic_components(str(self._root_sheet))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, _("Rescan failed"), str(e))
            return
        self.tree_dock.set_components(components)
        roles = sorted({c.role for c in components if c.role})
        clusters = sorted({c.cluster for c in components if c.cluster})
        self._set_combo_items(self.role_combo, roles)
        self._set_combo_items(self.cluster_combo, clusters)

    @staticmethod
    def _set_combo_items(combo: QComboBox, items: List[str]) -> None:
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        combo.setCurrentText(current)
        combo.blockSignals(False)

    # ── Target selection (leaf click / group click / live PCB selection) ──

    def _set_targets(self, refs: List[str]) -> None:
        self._current_targets = list(refs)
        self.target_label.setText(
            _("{count} selected: {refs}").format(count=len(refs), refs=", ".join(sorted(refs)))
            if refs else _("Nothing selected"))
        self.stage_button.setEnabled(bool(refs))

    def _on_group_picked(self, field: str, value: str, refs: List[str]) -> None:
        self._set_targets(refs)
        if field == "Role":
            self.role_combo.setCurrentText(value)
        elif field == "Cluster":
            self.cluster_combo.setCurrentText(value)

    def _on_stage(self) -> None:
        role = self.role_combo.currentText().strip()
        cluster = self.cluster_combo.currentText().strip()
        if not role and not cluster:
            QMessageBox.warning(self, _("Nothing to stage"),
                                _("Set Role and/or Cluster first."))
            return
        for ref in self._current_targets:
            if role:
                self.pending_dock.stage(ref, "Role", role)
            if cluster:
                self.pending_dock.stage(ref, "Cluster", cluster)

    # ── Live connection (staging only — never writes) ──────────────────────

    def _poll(self) -> None:
        if self.connection.is_connected:
            error = self.connection.refresh()
        else:
            error = self.connection.connect()
        self.status_label.setText(
            _("Not connected: {error}").format(error=error) if error
            else _("Connected (staging only — this tool never writes over IPC)"))

    def _poll_selection(self) -> None:
        if not self.connection.is_connected:
            return
        try:
            items = self.connection.board.adapter.get_selected_items()
        except Exception as e:
            logger.warning("Lost connection while polling selection: %s", e)
            self.connection.board = None
            return
        refs = {item.reference_field.text.value for item in items
                if isinstance(item, FootprintInstance)}
        self.tree_dock.highlight_refs(refs)
        if refs:
            self._set_targets(sorted(refs))

    # ── Apply (KiCad must be closed — instruction, not automation) ─────────

    def _on_apply(self) -> None:
        if self._root_sheet is None:
            QMessageBox.warning(self, _("No root sheet"), _("Pick a root sheet first."))
            return
        try:
            editing.check_kicad_not_running(force=False)
        except RuntimeError:
            QMessageBox.information(
                self, _("Close KiCad first"),
                _("KiCad appears to be running. Save your work and close KiCad, then click "
                  "Apply again — this tool never closes KiCad for you (see "
                  "fieldstool/__init__.py for why)."))
            return

        fields_cfg = self._pending_registry.as_fields_cfg()
        try:
            edits_by_file, file_texts, report = set_fields.plan_set_edits_for_root(
                str(self._root_sheet), fields_cfg)
        except FieldsToolError as e:
            QMessageBox.critical(self, _("Cannot apply"), str(e))
            return

        if not report:
            QMessageBox.information(self, _("Nothing to apply"),
                                    _("Every pending value already matches the schematic."))
            return

        summary = "\n".join(f"{','.join(r.refs)} .{r.field}: {r.old_value!r} -> {r.new_value!r}"
                            for r in report)
        confirmed = QMessageBox.question(
            self, _("Confirm apply"),
            _("About to write {count} change(s):\n\n{summary}").format(
                count=len(report), summary=summary))
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        written, failed = editing.write_files(edits_by_file, file_texts)
        if failed:
            QMessageBox.critical(self, _("Some files failed"),
                                 _("Restored from .bak: {failed}").format(failed=failed))
        else:
            QMessageBox.information(self, _("Applied"),
                                    _("{count} file(s) written.").format(count=len(written)))
            self._pending_registry.clear()
            self.pending_dock.refresh()
            self._rescan()
