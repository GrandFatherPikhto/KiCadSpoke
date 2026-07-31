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
"""
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from PyQt6.QtWidgets import (QDockWidget, QFormLayout, QGridLayout, QLabel,
                              QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
                              QWidget)

from kicadstamp.exceptions import PlacerError
from kicadstamp.explore import Selected
from kicadstamp.i18n import _
from kicadstamp.template_extraction import extract_template_from_selection

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

        layout.addWidget(QLabel(_("Net aliases (blank = keep literal):")))
        self._nets_container = QWidget()
        self._nets_layout = QGridLayout(self._nets_container)
        self._nets_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._nets_container)
        layout.addWidget(scroll, 1)

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
        self._update_button_state()

    def set_target_file(self, path: Optional[Path]) -> None:
        """Called by MainWindow whenever the Files dock's picked file
        changes (wired via FilePickerDock.on_pick_changed)."""
        self._target_path = path
        self.target_label.setText(
            _("Target: {path}").format(path=path) if path is not None
            else _("No target file picked (pick one in Files)"))
        self._update_button_state()

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

        board = self._main_window.connection.board
        if board is None:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(_("Not connected."))
            return

        net_template_map = {net: f"{{{edit.text().strip()}}}"
                             for net, edit in self._net_alias_edits.items() if edit.text().strip()}

        try:
            template_dict = extract_template_from_selection(
                board.adapter, name, net_template_map=net_template_map, items=self._raw_items)
        except PlacerError as e:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(str(e))
            return

        try:
            overwritten = self._write_merged(template_dict, name)
        except OSError as e:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(_("Write failed: {error}").format(error=e))
            return

        self.message_label.setStyleSheet(_SUCCESS_STYLE)
        action = _("Overwrote") if overwritten else _("Wrote")
        self.message_label.setText(
            _("{action} {name!r} in {path}").format(action=action, name=name, path=self._target_path))

    def _write_merged(self, template_dict: dict, name: str) -> bool:
        """Same read-merge-write shape as kicadstamp_cli.py's cmd_extract:
        existing entries in the target file are kept, only the one being
        written now is added/replaced — an extract target is routinely
        home to several cells accumulated over time, not exclusively owned
        by this one write."""
        is_json = self._target_path.suffix.lower() == '.json'
        existing: dict = {}
        if self._target_path.exists():
            with open(self._target_path, "r", encoding="utf-8") as f:
                existing = (json.load(f) if is_json else yaml.safe_load(f)) or {}
        overwritten = name in existing
        existing.update(template_dict)
        with open(self._target_path, "w", encoding="utf-8") as f:
            if is_json:
                json.dump(existing, f, indent=2, ensure_ascii=False, sort_keys=False)
            else:
                yaml.dump(existing, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        return overwritten
