# gui/docks/extract.py
"""
ExtractDock — build a Cell template from whatever's currently selected on
the board and write it into the file picked in the Files dock. Wraps
kicadstamp.template_extraction.extract_template_from_selection() plus
kicadstamp_cli.py's cmd_extract merge-into-existing-file behaviour — NOT
kicadstamp.author.dump_template(), which always overwrites the whole file
(fine for a script regenerating its own dedicated file, wrong here: a file
picked in the Files dock is very likely already home to other cells).

Fed by the same selection-watch timer as the tree/bulk-edit docks (see
MainWindow._poll_board_selection) — but unlike those, which only need
FootprintInstance refs, extraction needs the FULL raw selection (vias and
tracks too — a thermal via array or a decoupling-cap+via pattern has both),
so MainWindow passes this dock the raw get_selected_items() result
alongside the Selected-wrapped footprint list the other docks use.

Optionally also writes an extract_profiles: entry (the --profile mechanism
in kicadstamp_cli.py's extract command) — a replayable recipe (name/output/
params) so the same extraction can be re-run from the CLI later without
retyping the alias mapping by hand. This CANNOT go in the same file as the
cell output: cell_files/cells_file content is parsed as a flat
{cell_name: {...}} dict with no wrapper (see config/loader.py — every
top-level key is treated as a cell name), so an extract_profiles: sibling
key in that file would be misread as another cell. extract_profiles: lives
in a root/included profile config instead (alongside clone_placements:/
include:/cells_file: etc.) — a second, independent file target.

Net aliases feed params, NOT net_template_map, directly — the alias field
next to a net IS its params key (e.g. net '+2V5' aliased 'PWR_IN' becomes
params={'PWR_IN': '+2V5'}). extract_template_from_selection()'s own
auto-inference then derives net_template_map from params on its own
(net_resolution.py: any literal net equal to a param VALUE gets mapped to
that param's {key} automatically) — passing net_template_map directly
without matching params was an earlier bug here: parametrize_net() always
round-trip-checks pattern.format(**params) against the literal, and with
params=={} that check fails for every single alias (found live 2026-08-01,
"net '{X}' has a placeholder with no parameter" on every extract attempt
that used an alias).
"""
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from kipy.board_types import Via
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QFileDialog,
                              QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
                              QWidget)

from kicadstamp.exceptions import PlacerError
from kicadstamp.explore import Selected
from kicadstamp.i18n import _
from kicadstamp.template_extraction import extract_template_from_selection

from .. import settings
from ..docks.file_picker import PROJECT_ROOT

logger = logging.getLogger(__name__)

_ERROR_STYLE = "color: #a00;"
_WARN_STYLE = "color: #a60;"
_SUCCESS_STYLE = "color: #070;"


class ExtractDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__(_("Extract"), main_window)
        self._main_window = main_window
        self._raw_items: List[Any] = []
        self._selected_footprints: List[Selected] = []
        self._target_path: Optional[Path] = None
        self._net_alias_edits: Dict[str, QLineEdit] = {}
        self._profile_path: Optional[Path] = self._load_last_profile_path()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.selection_label = QLabel(_("Nothing selected"))
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)

        self.cluster_warning_label = QLabel("")
        self.cluster_warning_label.setWordWrap(True)
        self.cluster_warning_label.setStyleSheet(_WARN_STYLE)
        layout.addWidget(self.cluster_warning_label)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("cell name (key under cells:)"))
        form.addRow(_("Cell name:"), self.name_edit)
        layout.addLayout(form)

        self.target_label = QLabel(_("No target file picked (pick one in Files)"))
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        origin_form = QFormLayout()
        self.origin_mode_combo = QComboBox()
        self.origin_mode_combo.addItems(
            [_("Bounding box (default)"), _("Component role"), _("Via net")])
        self.origin_mode_combo.currentIndexChanged.connect(self._on_origin_mode_changed)
        origin_form.addRow(_("Origin:"), self.origin_mode_combo)
        layout.addLayout(origin_form)

        self._origin_role_row = QWidget()
        role_row = QHBoxLayout(self._origin_role_row)
        role_row.setContentsMargins(0, 0, 0, 0)
        self.origin_role_combo = QComboBox()
        self.origin_role_combo.setEditable(True)
        role_row.addWidget(QLabel(_("Role:")))
        role_row.addWidget(self.origin_role_combo, 1)
        self.origin_pad_edit = QLineEdit()
        self.origin_pad_edit.setPlaceholderText(_("pad (optional)"))
        role_row.addWidget(QLabel(_("Pad:")))
        role_row.addWidget(self.origin_pad_edit)
        layout.addWidget(self._origin_role_row)
        self._origin_role_row.setVisible(False)

        self._origin_via_row = QWidget()
        via_row = QHBoxLayout(self._origin_via_row)
        via_row.setContentsMargins(0, 0, 0, 0)
        self.origin_via_net_combo = QComboBox()
        self.origin_via_net_combo.setEditable(True)
        via_row.addWidget(QLabel(_("Net:")))
        via_row.addWidget(self.origin_via_net_combo, 1)
        layout.addWidget(self._origin_via_row)
        self._origin_via_row.setVisible(False)

        layout.addWidget(QLabel(_("Net aliases (blank = keep literal):")))
        self._nets_container = QWidget()
        self._nets_layout = QGridLayout(self._nets_container)
        self._nets_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._nets_container)
        layout.addWidget(scroll, 1)

        self.save_profile_checkbox = QCheckBox(_("Also save as extract_profile"))
        layout.addWidget(self.save_profile_checkbox)

        profile_form = QFormLayout()
        self.profile_key_edit = QLineEdit()
        self.profile_key_edit.setPlaceholderText(_("profile key (defaults to cell name)"))
        profile_form.addRow(_("Profile key:"), self.profile_key_edit)
        layout.addLayout(profile_form)

        profile_file_row = QHBoxLayout()
        self.profile_target_label = QLabel(self._display_path(self._profile_path)
                                            if self._profile_path else _("No profile file picked"))
        self.profile_target_label.setWordWrap(True)
        profile_file_row.addWidget(self.profile_target_label, 1)
        change_profile_button = QPushButton(_("Change profile file..."))
        change_profile_button.clicked.connect(self._on_change_profile_file)
        profile_file_row.addWidget(change_profile_button)
        layout.addLayout(profile_file_row)

        self.extract_button = QPushButton(_("Extract to file"))
        self.extract_button.setEnabled(False)
        self.extract_button.clicked.connect(self._on_extract)
        layout.addWidget(self.extract_button)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self.setWidget(container)

    def set_board_selection(self, raw_items: List[Any], selected_footprints: List[Selected]) -> None:
        """Called every selection-watch tick — see module docstring for why
        this needs the raw mixed list, not just the Selected-footprint one
        the tree/bulk-edit docks use."""
        self._raw_items = raw_items
        self._selected_footprints = selected_footprints
        self._update_selection_label()
        self._update_cluster_warning()
        self._rebuild_net_aliases()
        self._update_origin_choices()
        self._update_button_state()

    def _on_origin_mode_changed(self) -> None:
        mode = self.origin_mode_combo.currentIndex()
        self._origin_role_row.setVisible(mode == 1)
        self._origin_via_row.setVisible(mode == 2)

    def _update_origin_choices(self) -> None:
        """Populates the Role/Via-net combos from what's actually in the
        current selection — picking an origin from outside the selection
        makes no sense (extract_template_from_selection fatals on it
        anyway: 'role not found in selection' / 'no such via in selection'),
        so there's no point offering it."""
        roles = sorted({s.role for s in self._selected_footprints if s.role})
        self._set_combo_items(self.origin_role_combo, roles)

        via_nets = sorted({item.net.name for item in self._raw_items
                            if isinstance(item, Via) and item.net and item.net.name})
        self._set_combo_items(self.origin_via_net_combo, via_nets)

    @staticmethod
    def _set_combo_items(combo: QComboBox, items: List[str]) -> None:
        current_text = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        combo.setCurrentText(current_text)
        combo.blockSignals(False)

    def set_target_file(self, path: Optional[Path]) -> None:
        """Called by MainWindow whenever the Files dock's picked file
        changes (wired via FilePickerDock.on_pick_changed)."""
        self._target_path = path
        self.target_label.setText(
            _("Target: {path}").format(path=path) if path is not None
            else _("No target file picked (pick one in Files)"))
        self._update_button_state()

    @staticmethod
    def _load_last_profile_path() -> Optional[Path]:
        last = settings.load().get("last_profile_path")
        if last and Path(last).is_file():
            return Path(last)
        return None

    @staticmethod
    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(path)

    def _on_change_profile_file(self) -> None:
        start_dir = str(self._profile_path.parent) if self._profile_path else str(PROJECT_ROOT / "boards")
        chosen, _filter = QFileDialog.getSaveFileName(
            self, _("Pick extract_profiles target file"), start_dir,
            _("YAML/JSON files (*.yaml *.yml *.json)"),
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not chosen:
            return
        self._profile_path = Path(chosen)
        self.profile_target_label.setText(self._display_path(self._profile_path))
        data = settings.load()
        data["last_profile_path"] = chosen
        settings.save(data)

    def _update_selection_label(self) -> None:
        if not self._raw_items:
            self.selection_label.setText(_("Nothing selected"))
            return
        fp_count = len(self._selected_footprints)
        other_count = len(self._raw_items) - fp_count
        if other_count:
            self.selection_label.setText(
                _("{fp} component(s), {other} via/track(s) selected")
                .format(fp=fp_count, other=other_count))
        else:
            self.selection_label.setText(_("{fp} component(s) selected").format(fp=fp_count))

    def _update_cluster_warning(self) -> None:
        clusters = {s.cluster for s in self._selected_footprints}
        if len(clusters) > 1:
            shown = ", ".join(repr(c) for c in sorted(clusters, key=lambda c: c or ""))
            self.cluster_warning_label.setText(
                _("Selection spans multiple Clusters: {clusters}").format(clusters=shown))
        else:
            self.cluster_warning_label.setText("")

    def _rebuild_net_aliases(self) -> None:
        """One row per distinct net found on the selected components' pads.
        Preserves whatever the user already typed for a net that's still
        present — the selection-watch tick fires every ~400ms, so without
        this, in-progress typing would be wiped just like the tree/bulk-edit
        docks had to guard against."""
        nets = sorted({net for s in self._selected_footprints for net in s.nets.values()})
        previous = {net: edit.text() for net, edit in self._net_alias_edits.items()}
        if set(nets) == set(previous):
            return

        while self._nets_layout.count():
            item = self._nets_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._net_alias_edits = {}
        for row, net in enumerate(nets):
            self._nets_layout.addWidget(QLabel(net), row, 0)
            edit = QLineEdit()
            edit.setPlaceholderText(_("alias, e.g. PWR_IN"))
            edit.setText(previous.get(net, ""))
            self._nets_layout.addWidget(edit, row, 1)
            self._net_alias_edits[net] = edit

    def _update_button_state(self) -> None:
        self.extract_button.setEnabled(bool(self._raw_items) and self._target_path is not None)

    def _on_extract(self) -> None:
        self.message_label.setStyleSheet("")
        self.message_label.setText("")
        name = self.name_edit.text().strip()
        if not name:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(_("Cell name is required."))
            return
        if not self._raw_items or self._target_path is None:
            return
        save_profile = self.save_profile_checkbox.isChecked()
        if save_profile and self._profile_path is None:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(
                _("'Also save as extract_profile' is checked, but no profile file is picked."))
            return

        board = self._main_window.connection.board
        if board is None:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(_("Not connected."))
            return

        params: Dict[str, str] = {}
        for net_literal, edit in self._net_alias_edits.items():
            alias = edit.text().strip()
            if not alias:
                continue
            if alias in params:
                self.message_label.setStyleSheet(_ERROR_STYLE)
                self.message_label.setText(
                    _("Alias {alias!r} used for both {a!r} and {b!r} — each alias needs a "
                      "distinct net.").format(alias=alias, a=params[alias], b=net_literal))
                return
            params[alias] = net_literal

        origin_kwargs: Dict[str, str] = {}
        origin_mode = self.origin_mode_combo.currentIndex()
        if origin_mode == 1:  # component role (+ optional pad)
            role = self.origin_role_combo.currentText().strip()
            if not role:
                self.message_label.setStyleSheet(_ERROR_STYLE)
                self.message_label.setText(_("Origin: pick a component role."))
                return
            origin_kwargs["origin_component_role"] = role
            pad = self.origin_pad_edit.text().strip()
            if pad:
                origin_kwargs["origin_component_pad"] = pad
        elif origin_mode == 2:  # via net
            net = self.origin_via_net_combo.currentText().strip()
            if not net:
                self.message_label.setStyleSheet(_ERROR_STYLE)
                self.message_label.setText(_("Origin: pick a via net."))
                return
            origin_kwargs["origin_via_net"] = net

        try:
            template_dict = extract_template_from_selection(
                board.adapter, name, params=params, items=self._raw_items, **origin_kwargs)
        except PlacerError as e:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(str(e))
            return

        try:
            cell_overwritten = self._write_merged(self._target_path, template_dict)
        except OSError as e:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(_("Write failed: {error}").format(error=e))
            return

        messages = [_("{action} {name!r} in {path}").format(
            action=_("Overwrote") if cell_overwritten else _("Wrote"), name=name, path=self._target_path)]

        if save_profile:
            profile_key = self.profile_key_edit.text().strip() or name
            entry: Dict[str, Any] = {"output": self._display_path(self._target_path)}
            if profile_key != name:
                entry["name"] = name
            if params:
                entry["params"] = params
            for key, value in origin_kwargs.items():
                # Function kwargs (origin_component_role) vs. profile YAML
                # keys (origin_by_component_role) differ by "by_" — see
                # kicadstamp_cli.py's cmd_extract profile branch.
                entry[f"origin_by_{key[len('origin_'):]}"] = value
            try:
                profile_overwritten = self._write_merged(
                    self._profile_path, {"extract_profiles": {profile_key: entry}}, section="extract_profiles")
            except OSError as e:
                self.message_label.setStyleSheet(_ERROR_STYLE)
                self.message_label.setText(_("Cell written, but profile write failed: {error}").format(error=e))
                return
            messages.append(_("{action} profile {key!r} in {path}").format(
                action=_("overwrote") if profile_overwritten else _("wrote"),
                key=profile_key, path=self._profile_path))

        self.message_label.setStyleSheet(_SUCCESS_STYLE)
        self.message_label.setText("; ".join(messages))

    @staticmethod
    def _write_merged(path: Path, new_data: dict, section: Optional[str] = None) -> bool:
        """Same read-merge-write shape as kicadstamp_cli.py's cmd_extract:
        existing content in the target file is kept, only what's in
        new_data is added/replaced — a target file is routinely home to
        several cells/profiles accumulated over time, not exclusively owned
        by this one write.

        section=None: new_data is {cell_name: {...}} merged at the file's
        top level (the flat cell_files/cells_file shape).
        section='extract_profiles': new_data is
        {'extract_profiles': {key: {...}}} — only that one nested dict gets
        merged, every OTHER top-level key already in the file (clone_
        placements:, include:, cells_file:, ...) is left untouched.
        Returns whether the specific key being written already existed.
        """
        is_json = path.suffix.lower() == '.json'
        existing: dict = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                existing = (json.load(f) if is_json else yaml.safe_load(f)) or {}

        if section is None:
            key = next(iter(new_data))
            overwritten = key in existing
            existing.update(new_data)
        else:
            new_section = new_data[section]
            key = next(iter(new_section))
            target_section = existing.setdefault(section, {})
            overwritten = key in target_section
            target_section.update(new_section)

        with open(path, "w", encoding="utf-8") as f:
            if is_json:
                json.dump(existing, f, indent=2, ensure_ascii=False, sort_keys=False)
            else:
                yaml.dump(existing, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        return overwritten
