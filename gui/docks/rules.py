# gui/docks/rules.py
"""
RuleDock — edits a `rules:` entry (kicadstamp/config/models.py's Rule +
ManualSpoke): one shared anchor (Ref/Role, narrowed by Sheet/Cluster, or a
named Point) plus an ordered list of spokes, each placing a Cell at a
specific pad of that anchor with its own hand-tuned shift/rotation.
Requested live 2026-08-05 after Denis connected fpga_spokes.yaml/
fpga_cap_pair_spoke.yaml to a real project and hit the long-flagged gap
(config_tree.py's own module docstring) that Rules had no edit form at
all.

Table + detail-panel-below, not a form-per-spoke or inline table editing —
picked live over putting spokes in the shared Config tree (Denis: "может
её сразу в общем дереве?"): a spoke has no name field to hang a tree leaf
label on, spoke ORDER is semantically significant (ComponentPool consumes
spokes in list order — see ManualSpoke's own docstring), and a table's
columns show every spoke's shift/rotation/cluster at a glance, which a
tree of bare leaf labels could not. The table itself is read-only
(NoEditTriggers) — all editing goes through the detail row below and its
explicit Add/Update/Remove/Move buttons, so a row's displayed values can
never drift from what Add/Update actually validated and stored.

Origin has only two modes (Anchor ref/role / Point) — Rule has no `xy`
field at all, unlike Points/PlacerDock's three-way combo.

spoke.cell is a searchable combo (Denis: "Чтобы назначать разные целлы
разным спицам? Да, думаю комбобоксик") sourced from collect_all_cell_names()
(gui/docks/rename.py) — EVERY cells: key reachable from the project's root
via include:, not just this file's own (a spoke's cell routinely lives in
a different file than the rule referencing it). Point-chain (this dock's
own anchor AND has no per-spoke equivalent — spokes anchor to a pad on
THIS rule's own anchor, never to an arbitrary point) is populated the same
whole-graph way via collect_all_point_names(). Both need the PROJECT's
root path, not this dock's own target file (which follows file_selected
like Placer/ThermalVia) — set_root_path() is wired to ConfigTreeDock's
root_file_changed, the same second file-dependency Points' own docstring
flagged and deferred; here it's wired up front since the combo was
explicitly requested.

Redraw comes in two flavours (Denis: "Redraw Rule, Redraw (выбранная
спица) по-моему, так будет логично"):
  - Redraw Rule — the whole rule, all non-skipped spokes, same
    replace-by-name + ApplyPipeline(only=[...]) shape as ThermalViaArrayDock.
  - Redraw (selected spoke) — same, but every OTHER spoke gets a temporary
    skip=True injected into the copy handed to ApplyPipeline (never
    written back — Save is unaffected). Sound because spoke resolution
    shares ONE ComponentPool per net across the whole rule (see
    ManualPositionCalculator/ComponentPool) — you cannot resolve a single
    spoke in isolation without the rest of the pool, but you CAN ask the
    pipeline to skip every spoke except the one you want to see, which
    `skip:` already exists to do (see Rule's own docstring on skip vs
    retired).

Save writes via upsert_list_entry(key_fn=...) matching by
rule_effective_name (name if set, else net) — rules: is the one list
section without a REQUIRED name: field (see config/models.py's
rule_effective_name), unlike clone_placements:/thermal_via_arrays: which
always require one.
"""
import dataclasses
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from kipy.errors import ApiError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout,
                              QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                              QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import (Config, RuntimeContext, load_config, load_manual_spoke,
                               load_rule, rule_effective_name)
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.i18n import _

from ..worker import start_long_op
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, display_path, set_combo_items, show_message,
                      upsert_list_entry)
from .rename import collect_all_cell_names, collect_all_point_names

logger = logging.getLogger(__name__)

_COLUMNS = ["Pad", "Cell", "Shift X", "Shift Y", "Rotation", "Cluster", "Retired", "Skip"]


def _rule_identity(entry: Dict[str, Any]) -> Any:
    """upsert_list_entry's key_fn — mirrors rule_effective_name() at the
    raw-dict level (Save hasn't necessarily built a Rule object yet)."""
    return entry.get("name") or entry.get("net")


class RuleDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — same
    "plain QWidget, not its own QDockWidget" shape as Extract/Placer/
    Project/Thermal via/Points."""

    # Fired after a successful Save — ConfigTreeDock listens to refresh its
    # Rules category (see gui/dock_hub.py), same as every other dock here.
    saved = pyqtSignal()

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self._active_op: Optional[Any] = None
        self._path: Optional[Path] = None
        self._root_path: Optional[Path] = None
        self._spokes: List[Dict[str, Any]] = []
        self._selected_index: Optional[int] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.target_label = QLabel(_("No file picked (pick one in the Config tree)"))
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        rule_form = QFormLayout()
        self.net_edit = QComboBox()
        configure_searchable(self.net_edit)
        rule_form.addRow(_("Net:"), self.net_edit)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("optional — defaults to net for --only"))
        rule_form.addRow(_("Name:"), self.name_edit)
        layout.addLayout(rule_form)

        origin_form = QFormLayout()
        self.origin_mode_combo = QComboBox()
        self.origin_mode_combo.addItems([_("Anchor (ref/role)"), _("Point")])
        self.origin_mode_combo.currentIndexChanged.connect(self._on_origin_mode_changed)
        origin_form.addRow(_("Origin:"), self.origin_mode_combo)
        layout.addLayout(origin_form)

        self._anchor_row = QWidget()
        anchor_form = QFormLayout(self._anchor_row)
        anchor_form.setContentsMargins(0, 0, 0, 0)
        self.anchor_ref_edit = QLineEdit()
        self.anchor_ref_edit.setPlaceholderText(_("e.g. U3 (refdes — mostly avoided in this project)"))
        anchor_form.addRow(_("Ref:"), self.anchor_ref_edit)
        self.anchor_role_edit = QComboBox()
        configure_searchable(self.anchor_role_edit)
        anchor_form.addRow(_("Role:"), self.anchor_role_edit)
        self.anchor_sheet_edit = QLineEdit()
        self.anchor_sheet_edit.setPlaceholderText(_("sheet name (narrows an ambiguous Role, optional)"))
        anchor_form.addRow(_("Sheet:"), self.anchor_sheet_edit)
        self.anchor_cluster_edit = QComboBox()
        configure_searchable(self.anchor_cluster_edit)
        anchor_form.addRow(_("Anchor cluster:"), self.anchor_cluster_edit)
        layout.addWidget(self._anchor_row)

        self._point_row = QWidget()
        point_form = QFormLayout(self._point_row)
        point_form.setContentsMargins(0, 0, 0, 0)
        self.point_edit = QComboBox()
        configure_searchable(self.point_edit)
        point_form.addRow(_("Point:"), self.point_edit)
        layout.addWidget(self._point_row)

        checks_row = QHBoxLayout()
        self.retired_checkbox = QCheckBox(_("Retired"))
        self.skip_checkbox = QCheckBox(_("Skip"))
        checks_row.addWidget(self.retired_checkbox)
        checks_row.addWidget(self.skip_checkbox)
        layout.addLayout(checks_row)

        layout.addWidget(QLabel(_("Spokes:")))
        self.spokes_table = QTableWidget(0, len(_COLUMNS))
        self.spokes_table.setHorizontalHeaderLabels([_(c) for c in _COLUMNS])
        self.spokes_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.spokes_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.spokes_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.spokes_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.spokes_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        layout.addWidget(self.spokes_table, 1)

        move_row = QHBoxLayout()
        self.move_up_button = QPushButton(_("Move up"))
        self.move_up_button.clicked.connect(lambda: self._on_move_spoke(-1))
        move_row.addWidget(self.move_up_button)
        self.move_down_button = QPushButton(_("Move down"))
        self.move_down_button.clicked.connect(lambda: self._on_move_spoke(1))
        move_row.addWidget(self.move_down_button)
        layout.addLayout(move_row)

        spoke_form = QFormLayout()
        self.spoke_pad_edit = QLineEdit()
        self.spoke_pad_edit.setPlaceholderText(_("pad number on the rule's own anchor"))
        spoke_form.addRow(_("Pad:"), self.spoke_pad_edit)
        self.spoke_cell_combo = QComboBox()
        configure_searchable(self.spoke_cell_combo)
        spoke_form.addRow(_("Cell:"), self.spoke_cell_combo)
        self.spoke_cluster_combo = QComboBox()
        configure_searchable(self.spoke_cluster_combo)
        spoke_form.addRow(_("Cluster:"), self.spoke_cluster_combo)
        layout.addLayout(spoke_form)

        spoke_shift_row = QHBoxLayout()
        self.spoke_shift_x_edit = QLineEdit()
        self.spoke_shift_x_edit.setPlaceholderText(_("shift X mm (0)"))
        self.spoke_shift_y_edit = QLineEdit()
        self.spoke_shift_y_edit.setPlaceholderText(_("shift Y mm (0)"))
        spoke_shift_row.addWidget(QLabel(_("Shift X:")))
        spoke_shift_row.addWidget(self.spoke_shift_x_edit)
        spoke_shift_row.addWidget(QLabel(_("Shift Y:")))
        spoke_shift_row.addWidget(self.spoke_shift_y_edit)
        layout.addLayout(spoke_shift_row)

        spoke_extra_form = QFormLayout()
        self.spoke_rotation_edit = QLineEdit()
        self.spoke_rotation_edit.setPlaceholderText("0")
        spoke_extra_form.addRow(_("Rotation (deg):"), self.spoke_rotation_edit)
        layout.addLayout(spoke_extra_form)

        spoke_checks_row = QHBoxLayout()
        self.spoke_retired_checkbox = QCheckBox(_("Retired"))
        self.spoke_skip_checkbox = QCheckBox(_("Skip"))
        spoke_checks_row.addWidget(self.spoke_retired_checkbox)
        spoke_checks_row.addWidget(self.spoke_skip_checkbox)
        layout.addLayout(spoke_checks_row)

        spoke_button_row = QHBoxLayout()
        self.add_spoke_button = QPushButton(_("Add spoke"))
        self.add_spoke_button.clicked.connect(self._on_add_spoke)
        spoke_button_row.addWidget(self.add_spoke_button)
        self.update_spoke_button = QPushButton(_("Update selected"))
        self.update_spoke_button.clicked.connect(self._on_update_spoke)
        spoke_button_row.addWidget(self.update_spoke_button)
        self.remove_spoke_button = QPushButton(_("Remove selected"))
        self.remove_spoke_button.clicked.connect(self._on_remove_spoke)
        spoke_button_row.addWidget(self.remove_spoke_button)
        layout.addLayout(spoke_button_row)

        button_row = QHBoxLayout()
        self.redraw_rule_button = QPushButton(_("Redraw rule"))
        self.redraw_rule_button.clicked.connect(self._on_redraw_rule)
        button_row.addWidget(self.redraw_rule_button)
        self.redraw_spoke_button = QPushButton(_("Redraw selected spoke"))
        self.redraw_spoke_button.clicked.connect(self._on_redraw_spoke)
        button_row.addWidget(self.redraw_spoke_button)
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self._on_origin_mode_changed()
        self._refresh_table()

    # ── Wiring from the Config tree ─────────────────────────────────────

    def set_target_file(self, path: Optional[Path]) -> None:
        self._path = path
        self.target_label.setText(
            display_path(path) if path is not None
            else _("No file picked (pick one in the Config tree)"))

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to ConfigTreeDock's root_file_changed — the Cell/Point
        combos are sourced from the WHOLE include graph (see module
        docstring), which needs the project's root, not this dock's own
        target file."""
        self._root_path = path
        self._refresh_cell_names()
        self._refresh_point_names()

    def _refresh_cell_names(self) -> None:
        names = collect_all_cell_names(self._root_path) if self._root_path is not None else []
        set_combo_items(self.spoke_cell_combo, names)

    def _refresh_point_names(self) -> None:
        names = collect_all_point_names(self._root_path) if self._root_path is not None else []
        set_combo_items(self.point_edit, names)

    def refresh_known_roles(self, snapshot) -> None:
        """Same "populate from the live board" pattern as PlacerDock's own
        refresh_known_roles — called by DockHub.push_snapshot."""
        roles = sorted({s.role for s in snapshot if s.role})
        clusters = sorted({s.cluster for s in snapshot if s.cluster})
        set_combo_items(self.anchor_role_edit, roles)
        set_combo_items(self.anchor_cluster_edit, clusters)
        set_combo_items(self.spoke_cluster_combo, clusters)

    def refresh_known_nets(self, board) -> None:
        nets = sorted({n.name for n in board.adapter.get_all_nets() if n.name})
        set_combo_items(self.net_edit, nets)

    # ── Origin UI ─────────────────────────────────────────────────────────

    def _on_origin_mode_changed(self) -> None:
        mode = self.origin_mode_combo.currentIndex()
        self._anchor_row.setVisible(mode == 0)
        self._point_row.setVisible(mode == 1)

    # ── Message helper ────────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(self.message_label, text, style, logger)

    def _parse_float(self, edit: QLineEdit, label: str, default: float) -> Optional[float]:
        text = edit.text().strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            self._show_message(_("{label}: {text!r} is not a number.").format(label=label, text=text),
                               _ERROR_STYLE)
            return None

    # ── Spokes table ──────────────────────────────────────────────────────

    def _refresh_table(self) -> None:
        self.spokes_table.setRowCount(len(self._spokes))
        for row, spoke in enumerate(self._spokes):
            values = [
                str(spoke.get("pad", "")),
                str(spoke.get("cell", "")),
                str(spoke.get("shift_x_mm", "")) if spoke.get("shift_x_mm") else "",
                str(spoke.get("shift_y_mm", "")) if spoke.get("shift_y_mm") else "",
                str(spoke.get("rotation_deg", "")) if spoke.get("rotation_deg") else "",
                str(spoke.get("cluster", "")),
                _("yes") if spoke.get("retired") else "",
                _("yes") if spoke.get("skip") else "",
            ]
            for col, value in enumerate(values):
                self.spokes_table.setItem(row, col, QTableWidgetItem(value))

    def _on_table_selection_changed(self) -> None:
        rows = self.spokes_table.selectionModel().selectedRows()
        if not rows:
            self._selected_index = None
            return
        self._selected_index = rows[0].row()
        self._load_spoke_into_editor(self._spokes[self._selected_index])

    def _load_spoke_into_editor(self, spoke: Dict[str, Any]) -> None:
        self.spoke_pad_edit.setText(str(spoke.get("pad", "")))
        self.spoke_cell_combo.setCurrentText(str(spoke.get("cell", "")))
        self.spoke_shift_x_edit.setText(str(spoke.get("shift_x_mm", "")) if spoke.get("shift_x_mm") else "")
        self.spoke_shift_y_edit.setText(str(spoke.get("shift_y_mm", "")) if spoke.get("shift_y_mm") else "")
        self.spoke_rotation_edit.setText(str(spoke.get("rotation_deg", "")) if spoke.get("rotation_deg") else "")
        self.spoke_cluster_combo.setCurrentText(str(spoke.get("cluster", "")))
        self.spoke_retired_checkbox.setChecked(bool(spoke.get("retired", False)))
        self.spoke_skip_checkbox.setChecked(bool(spoke.get("skip", False)))

    def _clear_spoke_editor(self) -> None:
        self.spoke_pad_edit.setText("")
        self.spoke_cell_combo.setCurrentText("")
        self.spoke_shift_x_edit.setText("")
        self.spoke_shift_y_edit.setText("")
        self.spoke_rotation_edit.setText("")
        self.spoke_cluster_combo.setCurrentText("")
        self.spoke_retired_checkbox.setChecked(False)
        self.spoke_skip_checkbox.setChecked(False)

    def _build_spoke_dict(self) -> Optional[Dict[str, Any]]:
        pad = self.spoke_pad_edit.text().strip()
        if not pad:
            self._show_message(_("Pad is required."), _ERROR_STYLE)
            return None
        cell = self.spoke_cell_combo.currentText().strip()
        if not cell:
            self._show_message(_("Cell is required."), _ERROR_STYLE)
            return None

        entry: Dict[str, Any] = {"pad": pad, "cell": cell}
        shift_x = self._parse_float(self.spoke_shift_x_edit, _("Shift X"), default=0.0)
        if shift_x is None:
            return None
        if shift_x:
            entry["shift_x_mm"] = shift_x
        shift_y = self._parse_float(self.spoke_shift_y_edit, _("Shift Y"), default=0.0)
        if shift_y is None:
            return None
        if shift_y:
            entry["shift_y_mm"] = shift_y
        rotation = self._parse_float(self.spoke_rotation_edit, _("Rotation"), default=0.0)
        if rotation is None:
            return None
        if rotation:
            entry["rotation_deg"] = rotation
        cluster = self.spoke_cluster_combo.currentText().strip()
        if cluster:
            entry["cluster"] = cluster
        if self.spoke_retired_checkbox.isChecked():
            entry["retired"] = True
        if self.spoke_skip_checkbox.isChecked():
            entry["skip"] = True

        try:
            load_manual_spoke(entry, self.net_edit.currentText().strip() or "?")
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None
        return entry

    def _on_add_spoke(self) -> None:
        entry = self._build_spoke_dict()
        if entry is None:
            return
        self._spokes.append(entry)
        self._refresh_table()
        self.spokes_table.selectRow(len(self._spokes) - 1)
        self._show_message(_("Spoke added — remember to Save the rule."), _SUCCESS_STYLE)

    def _on_update_spoke(self) -> None:
        if self._selected_index is None:
            self._show_message(_("Pick a spoke row first."), _ERROR_STYLE)
            return
        entry = self._build_spoke_dict()
        if entry is None:
            return
        self._spokes[self._selected_index] = entry
        self._refresh_table()
        self.spokes_table.selectRow(self._selected_index)
        self._show_message(_("Spoke updated — remember to Save the rule."), _SUCCESS_STYLE)

    def _on_remove_spoke(self) -> None:
        if self._selected_index is None:
            self._show_message(_("Pick a spoke row first."), _ERROR_STYLE)
            return
        del self._spokes[self._selected_index]
        self._selected_index = None
        self._refresh_table()
        self._clear_spoke_editor()
        self._show_message(_("Spoke removed — remember to Save the rule."), _SUCCESS_STYLE)

    def _on_move_spoke(self, delta: int) -> None:
        if self._selected_index is None:
            self._show_message(_("Pick a spoke row first."), _ERROR_STYLE)
            return
        new_index = self._selected_index + delta
        if not (0 <= new_index < len(self._spokes)):
            return
        self._spokes[self._selected_index], self._spokes[new_index] = \
            self._spokes[new_index], self._spokes[self._selected_index]
        self._selected_index = new_index
        self._refresh_table()
        self.spokes_table.selectRow(new_index)

    # ── Building the Rule entry dict (shared by Redraw/Save) ────────────────

    def _build_rule_dict(self) -> Optional[Dict[str, Any]]:
        net = self.net_edit.currentText().strip()
        if not net:
            self._show_message(_("Net is required."), _ERROR_STYLE)
            return None

        entry: Dict[str, Any] = {"net": net, "spokes": list(self._spokes)}
        name = self.name_edit.text().strip()
        if name:
            entry["name"] = name

        mode = self.origin_mode_combo.currentIndex()
        if mode == 0:
            ref = self.anchor_ref_edit.text().strip()
            role = self.anchor_role_edit.currentText().strip()
            if not ref and not role:
                self._show_message(_("Anchor: set Ref or Role."), _ERROR_STYLE)
                return None
            if ref and role:
                self._show_message(_("Anchor: Ref and Role are mutually exclusive — set one."),
                                   _ERROR_STYLE)
                return None
            if ref:
                entry["anchor_ref"] = ref
            else:
                entry["anchor_role"] = role
                sheet = self.anchor_sheet_edit.text().strip()
                if sheet:
                    entry["anchor_sheet"] = sheet
            cluster = self.anchor_cluster_edit.currentText().strip()
            if cluster:
                entry["anchor_cluster"] = cluster
        else:  # Point
            point = self.point_edit.currentText().strip()
            if not point:
                self._show_message(_("Point: name is required."), _ERROR_STYLE)
                return None
            entry["anchor_point"] = point

        if self.retired_checkbox.isChecked():
            entry["retired"] = True
        if self.skip_checkbox.isChecked():
            entry["skip"] = True
        return entry

    # ── Redraw ────────────────────────────────────────────────────────────

    def _on_redraw_rule(self) -> None:
        self._show_message("")
        payload = self._collect_redraw_inputs(isolate_spoke_index=None)
        if payload is None:
            return
        self._start_redraw_op(payload)

    def _on_redraw_spoke(self) -> None:
        self._show_message("")
        if self._selected_index is None:
            self._show_message(_("Pick a spoke row first."), _ERROR_STYLE)
            return
        payload = self._collect_redraw_inputs(isolate_spoke_index=self._selected_index)
        if payload is None:
            return
        self._start_redraw_op(payload)

    def _collect_redraw_inputs(self, isolate_spoke_index: Optional[int]) -> Optional[Dict[str, Any]]:
        """UI thread: build+validate the current form's Rule and (for the
        selected-spoke variant) inject a temporary skip=True on every OTHER
        spoke — see module docstring on why this is safe (skip: already
        exists for exactly this narrowing, same as apply_pipeline.py's own
        drop_inactive_items)."""
        entry = self._build_rule_dict()
        if entry is None:
            return None
        if self._path is None:
            self._show_message(_("Pick a file in the Config tree first."), _ERROR_STYLE)
            return None

        try:
            rule = load_rule(entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None

        if isolate_spoke_index is not None:
            spokes = [dataclasses.replace(s, skip=(i != isolate_spoke_index))
                     for i, s in enumerate(rule.spokes)]
            rule = dataclasses.replace(rule, spokes=spokes)

        try:
            if self._path.exists():
                cfg, ctx = load_config(str(self._path))
            else:
                cfg, ctx = Config(), RuntimeContext()
        except (ValidationError, OSError, yaml.YAMLError) as e:
            self._show_message(_("Failed to load file: {error}").format(error=e), _ERROR_STYLE)
            return None

        effective = rule_effective_name(rule)
        # Replace-by-identity: previewing an already-saved rule's edits must
        # not create a second copy alongside the saved one.
        cfg.rules = [r for r in cfg.rules if rule_effective_name(r) != effective]
        cfg.rules.append(rule)

        return {"path": self._path, "cfg": cfg, "ctx": ctx, "name": effective}

    def _run_redraw(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: ApplyPipeline run only — never touches a widget."""
        pipeline = ApplyPipeline(config_path=str(payload["path"]),
                                 preloaded_cfg=payload["cfg"], preloaded_ctx=payload["ctx"],
                                 only=[payload["name"]], dry_run=False)
        try:
            pipeline.run()
        except (PlacerError, ValidationError, ApiError) as e:
            return {"error": _("Placement failed: {error}").format(error=e)}
        except Exception as e:
            logger.exception("Rule redraw failed")
            return {"error": _("Placement failed: {error}").format(error=e)}
        return {"name": payload["name"]}

    def _finish_redraw(self, result: Dict[str, Any]) -> None:
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        self._show_message(_("Placed {name!r}.").format(name=result["name"]), _SUCCESS_STYLE)

    def _start_redraw_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._main_window.connection,
            (self.redraw_rule_button, self.redraw_spoke_button, self.save_button),
            self._run_redraw, self._finish_redraw, self._on_redraw_failed, payload)

    def _on_redraw_failed(self, message: str) -> None:
        self._show_message(_("Placement failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_redraw_rule(self) -> None:
        """Synchronous composition of collect + run + finish — for tests."""
        payload = self._collect_redraw_inputs(isolate_spoke_index=None)
        if payload is None:
            return
        result = self._run_redraw(payload)
        self._finish_redraw(result)

    def _do_redraw_spoke(self) -> None:
        if self._selected_index is None:
            self._show_message(_("Pick a spoke row first."), _ERROR_STYLE)
            return
        payload = self._collect_redraw_inputs(isolate_spoke_index=self._selected_index)
        if payload is None:
            return
        result = self._run_redraw(payload)
        self._finish_redraw(result)

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        entry = self._build_rule_dict()
        if entry is None:
            return
        if self._path is None:
            self._show_message(_("Pick a file in the Config tree first."), _ERROR_STYLE)
            return

        try:
            load_rule(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return

        try:
            overwritten = upsert_list_entry(self._path, "rules", entry, key_fn=_rule_identity)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        self._show_message(
            _("{action} {name!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                name=_rule_identity(entry), path=display_path(self._path)),
            _SUCCESS_STYLE)
        self.saved.emit()

    # ── Starting a brand new entry (ConfigTreeDock's Add rule...) ───────────

    def new_rule(self, path: Path) -> None:
        """Resets the form to its initial (blank) state and targets path —
        ConfigTreeDock's "Add rule..." context-menu action opens this form
        empty, same reasoning as PlacerDock.new_placement()/
        ThermalViaArrayDock.new_thermal_via()/PointsDock.new_point()."""
        self.set_target_file(path)
        self.net_edit.setCurrentText("")
        self.name_edit.setText("")
        self.origin_mode_combo.setCurrentIndex(0)
        self._on_origin_mode_changed()
        self.anchor_ref_edit.setText("")
        self.anchor_role_edit.setCurrentText("")
        self.anchor_sheet_edit.setText("")
        self.anchor_cluster_edit.setCurrentText("")
        self.point_edit.setCurrentText("")
        self.retired_checkbox.setChecked(False)
        self.skip_checkbox.setChecked(False)
        self._spokes = []
        self._selected_index = None
        self._refresh_table()
        self._clear_spoke_editor()
        self._show_message("")

    # ── Loading an already-saved entry back into the form ───────────────────

    def load_entry(self, entry: Dict[str, Any]) -> None:
        """Reverse of _build_rule_dict() — called by ConfigTreeDock's Rules
        category (via rule_picked) when an already-saved entry is clicked,
        same shape as PlacerDock.load_placement/ThermalViaArrayDock.
        load_entry. rules: is a list section (see module docstring), so
        the payload is already the full dict — no re-read needed."""
        self._show_message("")
        self.net_edit.setCurrentText(str(entry.get("net", "")))
        self.name_edit.setText(str(entry.get("name") or ""))

        if "anchor_point" in entry:
            self.origin_mode_combo.setCurrentIndex(1)
            self.point_edit.setCurrentText(str(entry["anchor_point"]))
        else:
            self.origin_mode_combo.setCurrentIndex(0)
            self.anchor_ref_edit.setText(str(entry.get("anchor_ref", "")))
            self.anchor_role_edit.setCurrentText(str(entry.get("anchor_role", "")))
            self.anchor_sheet_edit.setText(str(entry.get("anchor_sheet", "")))
            self.anchor_cluster_edit.setCurrentText(str(entry.get("anchor_cluster", "")))
        self._on_origin_mode_changed()

        self.retired_checkbox.setChecked(bool(entry.get("retired", False)))
        self.skip_checkbox.setChecked(bool(entry.get("skip", False)))

        self._spokes = [dict(s) for s in (entry.get("spokes") or [])]
        self._selected_index = None
        self._refresh_table()
        self._clear_spoke_editor()
