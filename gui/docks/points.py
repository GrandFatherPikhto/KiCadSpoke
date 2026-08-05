# gui/docks/points.py
"""
PointsDock — edits a named, reusable `points:` entry (kicadstamp/config/
points.py's Point dataclass): a base position (Absolute XY / Anchor ref-
role, narrowed by Sheet/Pad/Cluster / chain to another named Point) plus an
optional flat mm shift. Requested live 2026-08-05 (Denis: "а можем мы
соорудить панельку с именованными поинтами?") after noticing how much of
PlacerDock's own Origin widget (gui/docks/placer.py) already matches
Point's shape field-for-field — Absolute XY/Anchor(ref/role)/Point are
exactly Point's three mutually exclusive anchor "bases", and shift_x_mm/
shift_y_mm is exactly PlacerDock's own Anchor/Point-mode shift.

Deliberately NOT sharing a widget with PlacerDock/ThermalViaArrayDock yet —
first pass, built to be used and judged by hand before generalizing
anything (Denis: "надо руками пощупать"); PlacerDock/ThermalViaArrayDock's
own Anchor rows are untouched here, including their still-missing Sheet
field (see anchor_sheet below) — extracting a shared Origin widget once
this shape is validated is a natural follow-up, not done in this pass.

anchor_sheet (Denis, 2026-08-05: "да, нужен anchor_sheet в этой панели") is
a plain QLineEdit here, same as anchor_ref — NOT yet a combo autocompleted
from kicadstamp.sheet_names.build_sheet_name_map(), because that map needs
the PROJECT's schematic_dir/schematic_files (RootMetadataDock/"Project"
tab's own fields — root_metadata.py), a second file dependency distinct
from "which file do points: get written to" (this dock's own
set_target_file, fed by file_selected same as Placer/ThermalVia). Left as
free text for this first pass rather than wiring a second cross-dock file
dependency before the core shape is validated.

Point-chain (`anchor_point`) IS autocompleted, though — from the currently
targeted file's own points: keys (self-contained, no cross-dock
dependency), closing the "points:-name autocomplete" gap docs/gui.md's
Placer section has flagged since 2026-08-03.

Save writes via merge_write(section="points") — points: is a DICT section
(keyed by name), unlike thermal_via_arrays:/clone_placements: (list
sections upsert_list_entry matches by an inline `name` field) — see
_common.py's merge_write docstring for the section= dict-merge shape this
uses, same as ExtractDock's cells:/extract_profiles: writes.

Resolve (Denis: "надо руками пощупать" — a way to check where a point
CURRENTLY computes to, before Save, without moving anything on the board —
Points have no physical effect of their own, unlike PlacerDock's Redraw)
calls kicadstamp.placement.services.point_resolver.resolve_point_chain(),
a standalone alternative to the full apply-time dependency-order pass
(dependency_order.py) that only walks this file's points: graph — so an
unrelated broken rule/clone_placement elsewhere in the same config file
can never block previewing an unrelated point (deliberately more lenient
than load_config()'s all-or-nothing validation). Resolved position shows
as text; if the point resolved through a live footprint (no shift
applied — see ResolvedPoint's own docstring), that footprint is also
selected on the live board via adapter.select_items(), the same highlight
mechanism the Components tree's own "click a node -> highlight on board"
already uses (kicadstamp/kicad/adapter.py's select_items). A bare xy point,
or one with a shift applied, has no footprint to highlight — text-only in
that case. sheet_names is passed as {} for now (same cross-dock-dependency
deferral as anchor_sheet's own free-text field above) — anchor_sheet is
saved correctly into the YAML either way, it just won't narrow ambiguity
in THIS panel's own Resolve preview yet (a real `apply`/CLI run already
builds sheet_names properly from the project's schematic_dir).
"""
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from kipy.errors import ApiError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QVBoxLayout, QWidget)

from kicadstamp.config import load_point
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.placement.services.point_resolver import resolve_point_chain
from kicadstamp.utils.units import MM

from .. import yaml_io
from ..worker import start_long_op
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      configure_searchable, display_path, merge_write, set_combo_items,
                      show_message)

logger = logging.getLogger(__name__)


class PointsDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — same
    "plain QWidget, not its own QDockWidget" shape as Extract/Placer/
    Project/Thermal via, see their module docstrings."""

    # Fired after a successful Save — ConfigTreeDock listens to refresh its
    # Points category (see gui/dock_hub.py), same as Placer/ThermalVia/Extract.
    saved = pyqtSignal()

    def __init__(self, main_window, connection=None):
        super().__init__(main_window)
        self._main_window = main_window
        self._connection = connection if connection is not None else main_window.connection
        self._active_op: Optional[Any] = None
        self._path: Optional[Path] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.target_label = QLabel(_("No file picked (pick one in the Config tree)"))
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        name_form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("name (referenced by anchor_point elsewhere)"))
        name_form.addRow(_("Name:"), self.name_edit)
        layout.addLayout(name_form)

        origin_form = QFormLayout()
        self.origin_mode_combo = QComboBox()
        self.origin_mode_combo.addItems([_("Absolute XY"), _("Anchor (ref/role)"), _("Point")])
        self.origin_mode_combo.currentIndexChanged.connect(self._on_origin_mode_changed)
        origin_form.addRow(_("Origin:"), self.origin_mode_combo)
        layout.addLayout(origin_form)

        self._xy_row = QWidget()
        xy_row = QHBoxLayout(self._xy_row)
        xy_row.setContentsMargins(0, 0, 0, 0)
        self.x_edit = QLineEdit()
        self.x_edit.setPlaceholderText(_("X mm"))
        self.y_edit = QLineEdit()
        self.y_edit.setPlaceholderText(_("Y mm"))
        xy_row.addWidget(QLabel(_("X:")))
        xy_row.addWidget(self.x_edit)
        xy_row.addWidget(QLabel(_("Y:")))
        xy_row.addWidget(self.y_edit)
        layout.addWidget(self._xy_row)

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
        self.anchor_pad_edit = QLineEdit()
        self.anchor_pad_edit.setPlaceholderText(_("pad (optional)"))
        anchor_form.addRow(_("Pad:"), self.anchor_pad_edit)
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

        self._shift_row = QWidget()
        shift_row = QHBoxLayout(self._shift_row)
        shift_row.setContentsMargins(0, 0, 0, 0)
        self.shift_x_edit = QLineEdit()
        self.shift_x_edit.setPlaceholderText(_("shift X mm (0)"))
        self.shift_y_edit = QLineEdit()
        self.shift_y_edit.setPlaceholderText(_("shift Y mm (0)"))
        shift_row.addWidget(QLabel(_("Shift X:")))
        shift_row.addWidget(self.shift_x_edit)
        shift_row.addWidget(QLabel(_("Shift Y:")))
        shift_row.addWidget(self.shift_y_edit)
        layout.addWidget(self._shift_row)

        button_row = QHBoxLayout()
        self.resolve_button = QPushButton(_("Resolve"))
        self.resolve_button.clicked.connect(self._on_resolve)
        button_row.addWidget(self.resolve_button)
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        layout.addStretch(1)
        self._on_origin_mode_changed()

    # ── Wiring from the Config tree ─────────────────────────────────────

    def set_target_file(self, path: Optional[Path]) -> None:
        self._path = path
        self.target_label.setText(
            display_path(path) if path is not None
            else _("No file picked (pick one in the Config tree)"))
        self._refresh_point_names()

    def refresh_known_roles(self, snapshot) -> None:
        """Same "populate from the live board" pattern as PlacerDock's own
        refresh_known_roles — called by DockHub.push_snapshot at the same
        ~2s poll cadence."""
        roles = sorted({s.role for s in snapshot if s.role})
        clusters = sorted({s.cluster for s in snapshot if s.cluster})
        set_combo_items(self.anchor_role_edit, roles)
        set_combo_items(self.anchor_cluster_edit, clusters)

    def _refresh_point_names(self) -> None:
        """Autocomplete for the Point-chain field, from the currently
        targeted file's own points: keys — self-contained (no live board,
        no other dock), closes docs/gui.md's long-flagged "points:-name
        autocomplete" gap for at least this dock's own Point mode."""
        names = []
        if self._path is not None:
            names = sorted((yaml_io.load_data(self._path).get("points") or {}).keys())
        set_combo_items(self.point_edit, names)

    # ── Origin UI ─────────────────────────────────────────────────────────

    def _on_origin_mode_changed(self) -> None:
        mode = self.origin_mode_combo.currentIndex()
        self._xy_row.setVisible(mode == 0)
        self._anchor_row.setVisible(mode == 1)
        self._point_row.setVisible(mode == 2)
        self._shift_row.setVisible(mode != 0)

    # ── Message helper ────────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(self.message_label, text, style, logger)

    # ── Building the Point entry dict (shared by Resolve/Save) ─────────────

    def _parse_float(self, edit: QLineEdit, label: str, default: float) -> Optional[float]:
        """Optional numeric field — blank text means "use default" (e.g.
        shift, which defaults to 0.0). None means invalid text — the error
        is already shown, caller must not overwrite it with a second
        message."""
        text = edit.text().strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            self._show_message(_("{label}: {text!r} is not a number.").format(label=label, text=text),
                               _ERROR_STYLE)
            return None

    def _parse_required_float(self, edit: QLineEdit, label: str) -> Optional[float]:
        """Required numeric field (Absolute XY's X/Y — there is no sensible
        default for a literal coordinate). None means either blank or
        invalid; the error is shown here in both cases."""
        text = edit.text().strip()
        if not text:
            self._show_message(_("{label} is required.").format(label=label), _ERROR_STYLE)
            return None
        try:
            return float(text)
        except ValueError:
            self._show_message(_("{label}: {text!r} is not a number.").format(label=label, text=text),
                               _ERROR_STYLE)
            return None

    def _build_entry(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Returns (name, data) — data matches _load_point()'s own shape
        (no 'name' key inside, unlike thermal_via_arrays/clone_placements'
        list entries — points: is keyed by name, see module docstring).
        None after showing the error."""
        name = self.name_edit.text().strip()
        if not name:
            self._show_message(_("Name is required."), _ERROR_STYLE)
            return None

        entry: Dict[str, Any] = {}
        mode = self.origin_mode_combo.currentIndex()
        if mode == 0:
            x = self._parse_required_float(self.x_edit, _("X"))
            if x is None:
                return None
            y = self._parse_required_float(self.y_edit, _("Y"))
            if y is None:
                return None
            entry["xy"] = [x, y]
        elif mode == 1:
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
            pad = self.anchor_pad_edit.text().strip()
            if pad:
                entry["anchor_pad"] = pad
        else:  # Point
            point = self.point_edit.currentText().strip()
            if not point:
                self._show_message(_("Point: name is required."), _ERROR_STYLE)
                return None
            entry["anchor_point"] = point

        if mode != 0:
            shift_x = self._parse_float(self.shift_x_edit, _("Shift X"), default=0.0)
            if shift_x is None:
                return None
            shift_y = self._parse_float(self.shift_y_edit, _("Shift Y"), default=0.0)
            if shift_y is None:
                return None
            if shift_x:
                entry["shift_x_mm"] = shift_x
            if shift_y:
                entry["shift_y_mm"] = shift_y

        return name, entry

    # ── Resolve ───────────────────────────────────────────────────────────

    def _on_resolve(self) -> None:
        self._show_message("")
        payload = self._collect_resolve_inputs()
        if payload is None:
            return
        self._start_resolve_op(payload)

    def _collect_resolve_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: build+validate the current form's Point, load every
        OTHER point already saved in this file (silently skipping any that
        fail to load — see module docstring on why an unrelated broken
        point must not block this preview), and hand the worker a plain-data
        payload (no widget references)."""
        built = self._build_entry()
        if built is None:
            return None
        name, entry = built

        try:
            point = load_point(name, entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None

        if self._path is None:
            self._show_message(_("Pick a file in the Config tree first."), _ERROR_STYLE)
            return None

        board = self._connection.board
        if board is None:
            self._show_message(_("Not connected."), _ERROR_STYLE)
            return None

        points = {}
        for other_name, other_data in (yaml_io.load_data(self._path).get("points") or {}).items():
            if other_name == name:
                continue
            try:
                points[other_name] = load_point(other_name, other_data or {})
            except ValidationError:
                continue  # unrelated broken entry — must not block this preview
        points[name] = point  # replace-by-name, same as PlacerDock/ThermalViaArrayDock's Redraw

        return {"name": name, "points": points, "board": board}

    def _run_resolve(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: board IPC only, never touches a widget."""
        adapter = payload["board"].adapter
        try:
            resolved = resolve_point_chain(adapter, payload["points"], payload["name"], sheet_names={})
        except (ValidationError, ApiError) as e:
            return {"error": _("Resolve failed: {error}").format(error=e)}
        if resolved.footprint is not None:
            adapter.select_items([resolved.footprint])
        return {
            "x_mm": resolved.position.x / MM,
            "y_mm": resolved.position.y / MM,
            "has_footprint": resolved.footprint is not None,
        }

    def _finish_resolve(self, result: Dict[str, Any]) -> None:
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        suffix = "" if result["has_footprint"] else _(" (no footprint to highlight)")
        self._show_message(
            _("X={x:.3f}mm Y={y:.3f}mm{suffix}").format(
                x=result["x_mm"], y=result["y_mm"], suffix=suffix),
            _SUCCESS_STYLE)

    def _start_resolve_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._main_window.connection, (self.resolve_button, self.save_button),
            self._run_resolve, self._finish_resolve, self._on_resolve_failed, payload)

    def _on_resolve_failed(self, message: str) -> None:
        self._show_message(_("Resolve failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_resolve(self) -> None:
        """Synchronous composition of collect + run + finish — for tests and
        any caller that must not return until Resolve is complete."""
        payload = self._collect_resolve_inputs()
        if payload is None:
            return
        result = self._run_resolve(payload)
        self._finish_resolve(result)

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        built = self._build_entry()
        if built is None:
            return
        name, entry = built
        if self._path is None:
            self._show_message(_("Pick a file in the Config tree first."), _ERROR_STYLE)
            return

        try:
            load_point(name, entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return

        try:
            overwritten = merge_write(self._path, {"points": {name: entry}}, section="points")
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        self._show_message(
            _("{action} {name!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                name=name, path=display_path(self._path)),
            _SUCCESS_STYLE)
        self._refresh_point_names()
        self.saved.emit()

    # ── Starting a brand new entry (ConfigTreeDock's Add point...) ──────────

    def new_point(self, path: Path) -> None:
        """Resets the form to its initial (blank) state and targets path —
        ConfigTreeDock's "Add point..." context-menu action opens this form
        empty, same reasoning as PlacerDock.new_placement()/ThermalViaArrayDock.
        new_thermal_via()."""
        self.set_target_file(path)
        self.name_edit.setText("")
        self.origin_mode_combo.setCurrentIndex(0)
        self._on_origin_mode_changed()
        self.x_edit.setText("")
        self.y_edit.setText("")
        self.anchor_ref_edit.setText("")
        self.anchor_role_edit.setCurrentText("")
        self.anchor_sheet_edit.setText("")
        self.anchor_pad_edit.setText("")
        self.anchor_cluster_edit.setCurrentText("")
        self.point_edit.setCurrentText("")
        self.shift_x_edit.setText("")
        self.shift_y_edit.setText("")
        self._show_message("")

    # ── Loading an already-saved entry back into the form ───────────────────

    def load_entry(self, name: str) -> None:
        """Reverse of _build_entry() — called by ConfigTreeDock's Points
        category (via points_picked) when an already-saved entry is
        clicked. points: is a DICT section (see module docstring), so the
        signal only carries the name — the actual data is re-read fresh
        from self._path here, same discipline root_metadata.py's
        set_target_file already uses."""
        self._show_message("")
        entry = {}
        if self._path is not None:
            entry = (yaml_io.load_data(self._path).get("points") or {}).get(name) or {}
        self.name_edit.setText(name)

        if "anchor_point" in entry:
            self.origin_mode_combo.setCurrentIndex(2)
            self.point_edit.setCurrentText(str(entry["anchor_point"]))
        elif "xy" in entry:
            self.origin_mode_combo.setCurrentIndex(0)
            xy = entry["xy"] or [0, 0]
            self.x_edit.setText(str(xy[0]))
            self.y_edit.setText(str(xy[1]))
        else:
            self.origin_mode_combo.setCurrentIndex(1)
            self.anchor_ref_edit.setText(str(entry.get("anchor_ref", "")))
            self.anchor_role_edit.setCurrentText(str(entry.get("anchor_role", "")))
            self.anchor_sheet_edit.setText(str(entry.get("anchor_sheet", "")))
            self.anchor_pad_edit.setText(str(entry.get("anchor_pad", "")))
            self.anchor_cluster_edit.setCurrentText(str(entry.get("anchor_cluster", "")))
        self._on_origin_mode_changed()

        self.shift_x_edit.setText(str(entry.get("shift_x_mm", "")) if entry.get("shift_x_mm") else "")
        self.shift_y_edit.setText(str(entry.get("shift_y_mm", "")) if entry.get("shift_y_mm") else "")
