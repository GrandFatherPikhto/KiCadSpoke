# gui/docks/root_metadata.py
"""
RootMetadataDock — edits the root-config-only scalar keys of whatever file
is currently browsed in the Config tree (gui/docks/config_tree.py):
layer, schematic_dir/schematic_files, registry_path/track_registry_path,
log_file, operation_log_dir, place_components/skip_existing_components,
via_keepout_clearance_mm/via_search_step_mm/via_search_max_radius_mm/
via_search_n_directions — exactly the field set config/models.py's Config
dataclass carries OUTSIDE cells/points/thermal_via_arrays/rules/
clone_placements (those are entity sections, already shown as tree leaves —
see _common.py's non_includable_keys(), the same set this dock edits).

Requested 2026-08-03 alongside the GUI tree roadmap's contextual-panel
direction ("Экстракт/Пласер/Рут становятся контекстными"): like Extract/
Placer, this dock follows ConfigTreeDock's file_selected signal rather than
a dedicated picker — whichever file is currently browsed is what gets
edited. These fields are only meaningful on a file used as an actual root
(an included file with any of them set is fatal at load time — see
config/includes.py), but the GUI does not gatekeep that here: a file can
legitimately serve as a root in one context and an include: fragment in
another (non_includable_keys()'s own docstring already documents this),
so Save always targets "whatever file is currently selected", same as
Extract/Placer.

Read-merge-write via _common.merge_write(section=None) — every other key
already in the file (cells:, include:, ...) is left untouched. A field is
only written if its value differs from Config's own dataclass default OR
the key was already present in the file when it was loaded (_present_keys)
— this keeps a freshly-created file free of default-value noise, while
never silently dropping a key the file already declared explicitly (even
one now typed back to its default). Actually clearing a key back to
"absent" is out of scope here — same "reachable by hand-editing the saved
YAML" spirit as PlacerDock's own documented scope limits.
"""
import dataclasses
import logging
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout,
                              QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from kicadstamp.config.models import Config
from kicadstamp.i18n import _

from .. import yaml_io
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      display_path, merge_write, show_message)

logger = logging.getLogger(__name__)

# Config's own field defaults — single source of truth, read via
# dataclasses instead of duplicated literals here (default_factory fields,
# e.g. schematic_files, are called; this dock only uses the list ones for
# schematic_files below).
_DEFAULTS: Dict[str, object] = {
    f.name: (f.default_factory() if f.default_factory is not dataclasses.MISSING else f.default)
    for f in dataclasses.fields(Config)
}

_TEXT_FIELDS = [
    ("schematic_dir", _("Schematic dir:")),
    ("registry_path", _("Registry path:")),
    ("track_registry_path", _("Track registry path:")),
    ("log_file", _("Log file:")),
    ("operation_log_dir", _("Operation log dir:")),
]
_BOOL_FIELDS = [
    ("place_components", _("Place components")),
    ("skip_existing_components", _("Skip existing components")),
]
_FLOAT_FIELDS = [
    ("via_keepout_clearance_mm", _("Via keepout clearance (mm):")),
    ("via_search_step_mm", _("Via search step (mm):")),
    ("via_search_max_radius_mm", _("Via search max radius (mm):")),
]
_INT_FIELDS = [
    ("via_search_n_directions", _("Via search directions:")),
]


class RootMetadataDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — used
    to be its own QDockWidget, merged 2026-08-03 (see ExtractDock's module
    docstring note for the same change). Layout builds directly on self
    instead of a wrapped QDockWidget-owned container; everything else is
    unchanged."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self._path: Optional[Path] = None
        self._present_keys: set = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.target_label = QLabel(_("No file picked (pick one in the Config tree)"))
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        form = QFormLayout()

        self.layer_combo = QComboBox()
        self.layer_combo.addItems(["F.Cu", "B.Cu"])
        form.addRow(_("Layer:"), self.layer_combo)

        self._text_edits: Dict[str, QLineEdit] = {}
        for key, label in _TEXT_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(_("(relative to this YAML)"))
            form.addRow(label, edit)
            self._text_edits[key] = edit

        self.schematic_files_edit = QLineEdit()
        self.schematic_files_edit.setPlaceholderText(_("comma-separated, relative to this YAML"))
        form.addRow(_("Schematic files:"), self.schematic_files_edit)

        self._bool_checks: Dict[str, QCheckBox] = {}
        for key, label in _BOOL_FIELDS:
            check = QCheckBox(label)
            form.addRow("", check)
            self._bool_checks[key] = check

        self._float_edits: Dict[str, QLineEdit] = {}
        for key, label in _FLOAT_FIELDS:
            edit = QLineEdit()
            form.addRow(label, edit)
            self._float_edits[key] = edit

        self._int_edits: Dict[str, QLineEdit] = {}
        for key, label in _INT_FIELDS:
            edit = QLineEdit()
            form.addRow(label, edit)
            self._int_edits[key] = edit

        layout.addLayout(form)

        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        layout.addWidget(self.save_button)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        layout.addStretch(1)

    # ── Wiring from the Config tree ─────────────────────────────────────

    def set_target_file(self, path: Optional[Path]) -> None:
        """Called whenever the Config tree's current file changes (wired to
        ConfigTreeDock's file_selected signal, same as Extract/Placer)."""
        self._show_message("")
        self._path = path
        if path is None:
            self.target_label.setText(_("No file picked (pick one in the Config tree)"))
            self._present_keys = set()
            self._populate({})
            return
        self.target_label.setText(display_path(path))
        data = yaml_io.load_data(path)
        self._present_keys = set(data.keys())
        self._populate(data)

    def _populate(self, data: dict) -> None:
        self.layer_combo.setCurrentText(data.get("layer", _DEFAULTS["layer"]))
        for key, _label in _TEXT_FIELDS:
            self._text_edits[key].setText(data.get(key) or "")
        self.schematic_files_edit.setText(", ".join(data.get("schematic_files") or []))
        for key, _label in _BOOL_FIELDS:
            self._bool_checks[key].setChecked(bool(data.get(key, _DEFAULTS[key])))
        for key, _label in _FLOAT_FIELDS:
            self._float_edits[key].setText(str(data.get(key, _DEFAULTS[key])))
        for key, _label in _INT_FIELDS:
            self._int_edits[key].setText(str(data.get(key, _DEFAULTS[key])))

    # ── Save ──────────────────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(self.message_label, text, style, logger)

    def _on_save(self) -> None:
        if self._path is None:
            self._show_message(_("Pick a file in the Config tree first."), _ERROR_STYLE)
            return

        updates: Dict[str, object] = {}

        layer = self.layer_combo.currentText()
        if layer != _DEFAULTS["layer"] or "layer" in self._present_keys:
            updates["layer"] = layer

        for key, _label in _TEXT_FIELDS:
            text = self._text_edits[key].text().strip()
            if text or key in self._present_keys:
                updates[key] = text or None

        files = [f.strip() for f in self.schematic_files_edit.text().split(",") if f.strip()]
        if files or "schematic_files" in self._present_keys:
            updates["schematic_files"] = files

        for key, _label in _BOOL_FIELDS:
            value = self._bool_checks[key].isChecked()
            if value != _DEFAULTS[key] or key in self._present_keys:
                updates[key] = value

        for key, label in _FLOAT_FIELDS:
            text = self._float_edits[key].text().strip()
            try:
                value = float(text) if text else _DEFAULTS[key]
            except ValueError:
                self._show_message(_("{label} {text!r} is not a number.")
                                    .format(label=label, text=text), _ERROR_STYLE)
                return
            if value != _DEFAULTS[key] or key in self._present_keys:
                updates[key] = value

        for key, label in _INT_FIELDS:
            text = self._int_edits[key].text().strip()
            try:
                value = int(text) if text else _DEFAULTS[key]
            except ValueError:
                self._show_message(_("{label} {text!r} is not an integer.")
                                    .format(label=label, text=text), _ERROR_STYLE)
                return
            if value != _DEFAULTS[key] or key in self._present_keys:
                updates[key] = value

        if not updates:
            self._show_message(_("Nothing to save — every field is still at its default."), "")
            return

        try:
            merge_write(self._path, updates)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        self._present_keys |= set(updates)
        self._show_message(
            _("Saved root metadata to {path}").format(path=display_path(self._path)), _SUCCESS_STYLE)
