# gui/docks/placer.py
"""
PlacerDock — pick a Cell + a Cluster name, an origin (absolute point /
anchor ref-or-role+pad / named Point), rotation, layer/mirror, and the net
params a by-nets placement needs — then Redraw (place it for real on the
live board, see it, adjust, Redraw again) and Save (write the
clone_placement into the Placer file). Requested live 2026-08-01:
"Суть пласера в том, чтобы выбрать кластер задать опорную точку...
Поменял координату, нажал перерисовать, оно переехало. Посмотрел,
утвердил."

Cluster tagging: ClonePlacement itself has NO output "Cluster" field —
checked directly against the pipeline (kicadstamp/placement/,
apply_pipeline.py): Cluster is only ever READ during apply (to narrow
anchor/role search), never written. The only place Cluster gets written
anywhere in this codebase is BulkFieldEditorDock's set_field_values_bulk()
call — placement and tagging were always two separate manual steps.
Redraw here closes that gap itself: after a successful ApplyPipeline run,
it independently replays the SAME item through a throwaway
PlacementPlanner (plan_item() is pure computation, doesn't move anything
— the real move already happened via the pipeline) to recover which refs
this placement actually touched, and tags Cluster=<name> on them via the
same set_field_values_bulk() BulkFieldEditorDock uses.

Redraw uses the REAL Placer file's full config (load_config), not a
synthetic single-placement one — critical for
PlacementRegistry.reconcile()'s known_anchor_ids protection
(kicadstamp/registry.py): built from a config missing every OTHER
clone_placement already on the board, a redraw preview would read as
"everything else is gone" and PRUNE their vias/tracks. Loading the real
file and only NARROWING execution via ApplyPipeline's own `only=` keeps
everyone else protected while still previewing just this one. The in-
progress (possibly unsaved) form state replaces-by-name whatever's
already in cfg.clone_placements for this name, so Redraw always previews
the CURRENT form, not last Save's.

config_path passed to ApplyPipeline is always the Placer file's own path
(even when preloaded_cfg is given) — it's still used to derive
registry_path/track_registry_path (registry_path_for_config) when the
config itself doesn't set them explicitly, which is what makes repeated
Redraws idempotent (a second click recognizes vias/tracks the first
click already created, via the SAME registry file a real
`kicadstamp_cli.py apply` on this file would also use).

Scope NOT covered by this first version (kept out deliberately, not by
oversight): anchor_sheet/anchor_cluster narrowing, anchor_point Point-name
autocomplete, refs: explicit role->ref override, by_selection mode. All
still reachable by hand-editing the saved YAML; add UI for them if they
turn out to be needed often.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from kipy.errors import ApiError
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QFormLayout,
                              QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                              QListWidget, QPushButton, QVBoxLayout, QWidget)

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import Config, RuntimeContext, _load_clone_placement, load_config
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.i18n import _
from kicadstamp.placement.planner import PlacementPlanner

from .. import yaml_io
from ..docks.file_picker import PROJECT_ROOT

logger = logging.getLogger(__name__)

_ERROR_STYLE = "color: #a00;"
_WARN_STYLE = "color: #a60;"
_SUCCESS_STYLE = "color: #070;"

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class PlacerDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__(_("Placer"), main_window)
        self._main_window = main_window
        self._cells_path: Optional[Path] = None
        self._placer_path: Optional[Path] = None
        self._selected_cell: Optional[str] = None
        self._param_edits: Dict[str, QLineEdit] = {}

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel(_("Cell:")))
        self.cells_list = QListWidget()
        self.cells_list.setMaximumHeight(90)
        self.cells_list.itemClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.cells_list)
        self.cell_label = QLabel(_("No cell picked"))
        self.cell_label.setWordWrap(True)
        layout.addWidget(self.cell_label)

        form = QFormLayout()
        self.cluster_edit = QLineEdit()
        self.cluster_edit.setPlaceholderText(_("Cluster / clone_placement name"))
        form.addRow(_("Cluster:"), self.cluster_edit)
        layout.addLayout(form)

        layout.addWidget(QLabel(_("Params (placeholder -> literal net, for by-nets role resolution):")))
        self._params_container = QWidget()
        self._params_layout = QGridLayout(self._params_container)
        self._params_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._params_container)

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
        anchor_form.addRow(_("Ref:"), self.anchor_ref_edit)
        self.anchor_role_edit = QLineEdit()
        anchor_form.addRow(_("Role:"), self.anchor_role_edit)
        self.anchor_pad_edit = QLineEdit()
        self.anchor_pad_edit.setPlaceholderText(_("pad (optional)"))
        anchor_form.addRow(_("Pad:"), self.anchor_pad_edit)
        layout.addWidget(self._anchor_row)

        self._point_row = QWidget()
        point_form = QFormLayout(self._point_row)
        point_form.setContentsMargins(0, 0, 0, 0)
        self.point_edit = QLineEdit()
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

        extra_form = QFormLayout()
        self.rotation_edit = QLineEdit()
        self.rotation_edit.setPlaceholderText("0")
        extra_form.addRow(_("Rotation (deg):"), self.rotation_edit)
        self.layer_combo = QComboBox()
        self.layer_combo.addItems([_("(cell default)"), "F.Cu", "B.Cu"])
        extra_form.addRow(_("Layer:"), self.layer_combo)
        layout.addLayout(extra_form)
        self.mirror_checkbox = QCheckBox(_("Mirror"))
        layout.addWidget(self.mirror_checkbox)

        button_row = QHBoxLayout()
        self.redraw_button = QPushButton(_("Redraw"))
        self.redraw_button.clicked.connect(self._on_redraw)
        button_row.addWidget(self.redraw_button)
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        layout.addStretch(1)
        self.setWidget(container)
        self._on_origin_mode_changed()

    # ── Wiring from the Files dock ───────────────────────────────────────

    def set_cells_file(self, path: Optional[Path]) -> None:
        self._cells_path = path
        self._refresh_cells_list()

    def set_placer_file(self, path: Optional[Path]) -> None:
        self._placer_path = path

    def _refresh_cells_list(self) -> None:
        self.cells_list.clear()
        self.cells_list.addItems(sorted(yaml_io.existing_keys(self._cells_path)))

    def _on_cell_clicked(self, item) -> None:
        self._selected_cell = item.text()
        self.cell_label.setText(_("Cell: {name}").format(name=self._selected_cell))
        self._rebuild_param_rows()

    def _rebuild_param_rows(self) -> None:
        cell_data = yaml_io.load_data(self._cells_path).get(self._selected_cell, {})
        placeholders = sorted(self._discover_placeholders(cell_data))
        previous = {name: edit.text() for name, edit in self._param_edits.items()}

        while self._params_layout.count():
            item = self._params_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._param_edits = {}
        for row, name in enumerate(placeholders):
            self._params_layout.addWidget(QLabel(name), row, 0)
            edit = QLineEdit()
            edit.setPlaceholderText(_("literal net for {{{name}}}").format(name=name))
            edit.setText(previous.get(name, ""))
            self._params_layout.addWidget(edit, row, 1)
            self._param_edits[name] = edit

    @staticmethod
    def _discover_placeholders(node: Any) -> set:
        found = set()
        if isinstance(node, dict):
            for value in node.values():
                found |= PlacerDock._discover_placeholders(value)
        elif isinstance(node, list):
            for value in node:
                found |= PlacerDock._discover_placeholders(value)
        elif isinstance(node, str):
            found |= set(_PLACEHOLDER_RE.findall(node))
        return found

    # ── Origin UI ─────────────────────────────────────────────────────────

    def _on_origin_mode_changed(self) -> None:
        mode = self.origin_mode_combo.currentIndex()
        self._xy_row.setVisible(mode == 0)
        self._anchor_row.setVisible(mode == 1)
        self._point_row.setVisible(mode == 2)
        self._shift_row.setVisible(mode in (1, 2))

    # ── Message helper (same shape as ExtractDock/BulkFieldEditorDock) ──────

    def _show_message(self, text: str, style: str = "") -> None:
        self.message_label.setStyleSheet(style)
        self.message_label.setText(text)
        if not text:
            return
        if style == _ERROR_STYLE:
            logger.error(text)
        elif style == _WARN_STYLE:
            logger.warning(text)
        else:
            logger.info(text)

    @staticmethod
    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(path)

    # ── Building the clone_placement dict (shared by Redraw and Save) ──────

    def _parse_float(self, edit: QLineEdit, label: str, default: Optional[float] = None) -> Optional[float]:
        text = edit.text().strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            self._show_message(_("{label}: {text!r} is not a number.").format(label=label, text=text), _ERROR_STYLE)
            return None

    def _build_entry_dict(self) -> Optional[Dict[str, Any]]:
        name = self.cluster_edit.text().strip()
        if not name:
            self._show_message(_("Cluster name is required."), _ERROR_STYLE)
            return None
        if not self._selected_cell:
            self._show_message(_("Pick a Cell first."), _ERROR_STYLE)
            return None

        entry: Dict[str, Any] = {"name": name, "cell": self._selected_cell}

        mode = self.origin_mode_combo.currentIndex()
        if mode == 0:
            x = self._parse_float(self.x_edit, "X")
            y = self._parse_float(self.y_edit, "Y")
            if x is None or y is None:
                return None
            entry["xy"] = [x, y]
        else:
            shift_x = self._parse_float(self.shift_x_edit, _("Shift X"), default=0.0)
            shift_y = self._parse_float(self.shift_y_edit, _("Shift Y"), default=0.0)
            if shift_x is None or shift_y is None:
                return None
            entry["xy"] = [shift_x, shift_y]
            if mode == 1:
                ref = self.anchor_ref_edit.text().strip()
                role = self.anchor_role_edit.text().strip()
                pad = self.anchor_pad_edit.text().strip()
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
                if pad:
                    entry["anchor_pad"] = pad
            else:  # Point
                point = self.point_edit.text().strip()
                if not point:
                    self._show_message(_("Point: name is required."), _ERROR_STYLE)
                    return None
                entry["anchor_point"] = point

        rotation = self._parse_float(self.rotation_edit, _("Rotation"), default=0.0)
        if rotation is None:
            return None
        if rotation:
            entry["rotation_deg"] = rotation

        layer_idx = self.layer_combo.currentIndex()
        if layer_idx == 1:
            entry["layer"] = "F.Cu"
        elif layer_idx == 2:
            entry["layer"] = "B.Cu"

        if self.mirror_checkbox.isChecked():
            entry["mirror"] = True

        params = {name: edit.text().strip() for name, edit in self._param_edits.items() if edit.text().strip()}
        if params:
            entry["params"] = params

        return entry

    # ── Redraw ────────────────────────────────────────────────────────────

    def _on_redraw(self) -> None:
        self._show_message("")
        entry = self._build_entry_dict()
        if entry is None:
            return
        if self._placer_path is None:
            self._show_message(_("Pick a Placer file in Files first."), _ERROR_STYLE)
            return
        if self._cells_path is None:
            self._show_message(_("Pick a Cells file in Files first."), _ERROR_STYLE)
            return

        try:
            clone_placement = _load_clone_placement(entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return

        try:
            if self._placer_path.exists():
                cfg, ctx = load_config(str(self._placer_path))
            else:
                cfg, ctx = Config(), RuntimeContext()
        except (ValidationError, OSError, yaml.YAMLError) as e:
            self._show_message(_("Failed to load Placer file: {error}").format(error=e), _ERROR_STYLE)
            return

        if self._selected_cell not in cfg.cells:
            self._show_message(
                _("Cell {cell!r} isn't reachable from the Placer file's cell_files: — "
                  "extract/save it and make sure cell_files: is wired (see Extract).")
                .format(cell=self._selected_cell), _ERROR_STYLE)
            return

        # Replace-by-name: previewing an already-saved placement's edits
        # must not create a second copy alongside the saved one.
        cfg.clone_placements = [c for c in cfg.clone_placements if c.name != clone_placement.name]
        cfg.clone_placements.append(clone_placement)

        pipeline = ApplyPipeline(config_path=str(self._placer_path), preloaded_cfg=cfg, preloaded_ctx=ctx,
                                  only=[clone_placement.name], dry_run=False)
        try:
            pipeline.run()
        except (PlacerError, ValidationError, ApiError) as e:
            self._show_message(_("Placement failed: {error}").format(error=e), _ERROR_STYLE)
            return
        except Exception as e:
            logger.exception("Placer redraw failed")
            self._show_message(_("Placement failed: {error}").format(error=e), _ERROR_STYLE)
            return

        try:
            tagged = self._tag_cluster(pipeline, cfg, ctx, clone_placement.name)
        except Exception as e:
            logger.exception("Cluster tagging after placement failed")
            self._show_message(_("Placed, but tagging Cluster failed: {error}").format(error=e), _WARN_STYLE)
            return

        self._show_message(
            _("Placed {name!r} ({count} component(s) tagged Cluster={name!r}).")
            .format(name=clone_placement.name, count=tagged), _SUCCESS_STYLE)

    def _tag_cluster(self, pipeline: ApplyPipeline, cfg: Config, ctx: RuntimeContext, name: str) -> int:
        """Recovers which refs this specific clone_placement touched (see
        module docstring) and tags them Cluster=name. Returns how many
        got tagged (0 if the item couldn't be found — shouldn't happen
        given `only=[name]` just ran successfully, but not fatal either
        way, since the board is already correctly placed regardless)."""
        my_item = next((it for it in pipeline.items
                         if it.kind == 'clone' and it.obj.name == name), None)
        if my_item is None:
            return 0

        planner = PlacementPlanner(pipeline.adapter, cfg, sheet_names=ctx.sheet_names if ctx else {})
        planner.begin_planning()
        refs: List[str] = []
        for item in pipeline.items:
            moves = planner.plan_item(item)
            if item is my_item:
                refs = [m.ref for m in moves]
                break

        updates = []
        for ref in refs:
            fp = pipeline.adapter.get_footprint(ref)
            if fp is not None:
                updates.append((fp, CLUSTER_FIELD_NAME, name))
        if updates:
            pipeline.adapter.set_field_values_bulk(updates, _("Placer: tag Cluster={name}").format(name=name))
        return len(updates)

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        entry = self._build_entry_dict()
        if entry is None:
            return
        if self._placer_path is None:
            self._show_message(_("Pick a Placer file in Files first."), _ERROR_STYLE)
            return
        try:
            _load_clone_placement(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return

        try:
            overwritten = self._upsert_clone_placement(self._placer_path, entry)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        self._show_message(
            _("{action} {name!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                name=entry["name"], path=self._display_path(self._placer_path)),
            _SUCCESS_STYLE)

    @staticmethod
    def _upsert_clone_placement(path: Path, entry: Dict[str, Any]) -> bool:
        """Read-merge-write like ExtractDock's _write_merged()/
        _add_list_entry(), but for clone_placements: — a list of dicts
        matched by their own 'name' key, not by list membership: an entry
        whose name already exists gets REPLACED in place (same position),
        a new name gets appended. Every other key in the file (cells:,
        cell_files:, include:, extract_profiles:, ...) is left untouched."""
        is_json = path.suffix.lower() == '.json'
        existing: dict = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                existing = (json.load(f) if is_json else yaml.safe_load(f)) or {}

        items = existing.setdefault("clone_placements", [])
        if not isinstance(items, list):
            raise OSError(_("clone_placements: in {path} is not a list — refusing to touch it")
                          .format(path=path))

        overwritten = False
        for i, existing_entry in enumerate(items):
            if isinstance(existing_entry, dict) and existing_entry.get("name") == entry["name"]:
                items[i] = entry
                overwritten = True
                break
        if not overwritten:
            items.append(entry)

        with open(path, "w", encoding="utf-8") as f:
            if is_json:
                json.dump(existing, f, indent=2, ensure_ascii=False, sort_keys=False)
            else:
                yaml.dump(existing, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        return overwritten
