# gui/docks/root_metadata.py
"""
RootMetadataDock — edits the root-config-only scalar keys of the project's
ONE root file: layer, schematic_dir/schematic_files/root_sheet, registry_path/
track_registry_path, log_file, operation_log_dir, place_components/
skip_existing_components, via_keepout_clearance_mm/via_search_step_mm/
via_search_max_radius_mm/via_search_n_directions — exactly the field set
config/models.py's Config dataclass carries OUTSIDE cells/points/
thermal_via_arrays/rules/clone_placements (those are entity sections,
already shown as tree leaves — see _common.py's non_includable_keys(), the
same set this dock edits).

root_sheet (added 2026-08-07, Denis: "рутовый шит надо перетащить хотя бы
в настройки проекта") — used to live only in the GUI's fieldstool dock as a
QSettings value, global rather than per-project, so switching projects
silently kept the previous project's root sheet and fieldstool's Pending
changes diff matched nothing (see config/models.py's Config.root_sheet
docstring). Wired to FieldsToolDock.set_root_path via ConfigTreeDock's
root_file_changed, same as rules_dock/placer_dock/etc.'s own set_root_path —
the project's value takes over as soon as a root file with it set is
opened; an empty/absent value leaves fieldstool's current root sheet (manual
Pick, or the old global QSettings one) alone, so existing profiles that
haven't adopted this field yet see no change in behavior.

Requested 2026-08-03 alongside the GUI tree roadmap's contextual-panel
direction ("Экстракт/Пласер/Рут становятся контекстными"). Originally
followed ConfigTreeDock's file_selected like Extract/Placer (whatever file
was currently browsed), but changed 2026-08-05 (Denis: "root-панель должна
читать/хранить/править настройки текущего проекта, независимо от того,
выбран узел root или нет. Он у нас единственный") to follow the dedicated
root_file_changed signal instead — it always targets the project's actual
root file, regardless of which included file is currently selected in the
tree. This also matches the field semantics better: these keys are only
meaningful on a file used as an actual root (an included file with any of
them set is fatal at load time — see config/includes.py), so tracking
"whatever is clicked" could point this dock at a file where writing these
fields would be invalid.

Restructured into tabs the same day ("решил сделать root табами") to cut
dock height, following ExtractDock's 2026-08-04 tabbing for the same
reason — Layer/place_components/skip_existing_components stay above the
tabs as general project settings; Files/Schematics/Via each get their own
tab.

Read-merge-write via _common.merge_write(section=None) — every other key
already in the file (cells:, include:, ...) is left untouched. A field is
only written if its value differs from Config's own dataclass default OR
the key was already present in the file when it was loaded (_present_keys)
— this keeps a freshly-created file free of default-value noise, while
never silently dropping a key the file already declared explicitly (even
one now typed back to its default). Actually clearing a key back to
"absent" is out of scope here — same "reachable by hand-editing the saved
YAML" spirit as PlacerDock's own documented scope limits.

Path fields get a "..." browse button (2026-08-03, Denis: "надо бы кнопки
с диалогом выбора пути/файла") — schematic_dir/operation_log_dir pick an
existing DIRECTORY (QFileDialog.getExistingDirectory); registry_path/
track_registry_path/log_file pick a FILE via a Save-mode dialog (these
files are routinely created BY `apply` on first run, not beforehand — same
"may not exist yet" reasoning ConfigTreeDock's own Add-included-file
dialog already uses). Whatever absolute path the dialog returns is
converted to a path relative to the target file's own directory before
being written into the field — every one of these fields is documented as
"relative to this YAML" (see config/models.py's Config docstring).

schematic_files (a real list, not a single scalar) gets a QListWidget +
Add.../Remove instead of a comma-separated QLineEdit (2026-08-03, Denis:
"диалоговое окошко со списком и кнопки добавить/удалить с диалогом выбора
файла") — Add opens an Open-mode dialog filtered to *.kicad_sch (these
files must already exist, unlike the path fields above), Remove deletes
whatever's currently selected.
"""
import dataclasses
import logging
import os
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QPushButton, QTabWidget, QVBoxLayout, QWidget)

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

# Third element: "dir" -> browse button opens getExistingDirectory,
# "file" -> Save-mode getSaveFileName (these are routinely created BY apply
# on first run, so they may not exist yet — see module docstring), "sch" ->
# Open-mode getOpenFileName filtered to *.kicad_sch (this one must already
# exist, same reasoning as schematic_files' own Add... button below).
# Fourth element: which tab it lands in (2026-08-05 tabbing) — schematic_dir/
# root_sheet sit with schematic_files on the "Schematics" tab, the other
# four (none of which are schematic-specific) share the "Files" tab.
_TEXT_FIELDS = [
    ("schematic_dir", _("Schematic dir:"), "dir", "schematics"),
    ("root_sheet", _("Root sheet:"), "sch", "schematics"),
    ("registry_path", _("Registry path:"), "file", "files"),
    ("track_registry_path", _("Track registry path:"), "file", "files"),
    ("log_file", _("Log file:"), "file", "files"),
    ("operation_log_dir", _("Operation log dir:"), "dir", "files"),
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

        self.target_label = QLabel(_("No project file open"))
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        # General project settings — apply regardless of tab, shown above
        # them rather than forced into one of Files/Schematics/Via.
        common_form = QFormLayout()

        self.layer_combo = QComboBox()
        self.layer_combo.addItems(["F.Cu", "B.Cu"])
        common_form.addRow(_("Layer:"), self.layer_combo)

        self._bool_checks: Dict[str, QCheckBox] = {}
        for key, label in _BOOL_FIELDS:
            check = QCheckBox(label)
            common_form.addRow("", check)
            self._bool_checks[key] = check

        layout.addLayout(common_form)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)

        files_page = QWidget()
        files_form = QFormLayout(files_page)
        schematics_page = QWidget()
        schematics_form = QFormLayout(schematics_page)
        via_page = QWidget()
        via_form = QFormLayout(via_page)

        self._text_edits: Dict[str, QLineEdit] = {}
        for key, label, kind, group in _TEXT_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(_("(relative to this YAML)"))
            row = QHBoxLayout()
            row.addWidget(edit)
            browse_button = QPushButton("...")
            browse_button.setMaximumWidth(30)
            if kind == "dir":
                browse_button.clicked.connect(
                    lambda _checked=False, e=edit, l=label: self._browse_dir(e, l))
            elif kind == "sch":
                browse_button.clicked.connect(
                    lambda _checked=False, e=edit, l=label: self._browse_sch(e, l))
            else:
                browse_button.clicked.connect(
                    lambda _checked=False, e=edit, l=label: self._browse_file(e, l))
            row.addWidget(browse_button)
            target_form = schematics_form if group == "schematics" else files_form
            target_form.addRow(label, row)
            self._text_edits[key] = edit

        schematic_files_container = QWidget()
        sf_layout = QVBoxLayout(schematic_files_container)
        sf_layout.setContentsMargins(0, 0, 0, 0)
        self.schematic_files_list = QListWidget()
        self.schematic_files_list.setMaximumHeight(80)
        sf_layout.addWidget(self.schematic_files_list)
        sf_buttons = QHBoxLayout()
        sf_add_button = QPushButton(_("Add..."))
        sf_add_button.clicked.connect(self._add_schematic_file)
        sf_remove_button = QPushButton(_("Remove"))
        sf_remove_button.clicked.connect(self._remove_schematic_file)
        sf_buttons.addWidget(sf_add_button)
        sf_buttons.addWidget(sf_remove_button)
        sf_layout.addLayout(sf_buttons)
        schematics_form.addRow(_("Schematic files:"), schematic_files_container)

        self._float_edits: Dict[str, QLineEdit] = {}
        for key, label in _FLOAT_FIELDS:
            edit = QLineEdit()
            via_form.addRow(label, edit)
            self._float_edits[key] = edit

        self._int_edits: Dict[str, QLineEdit] = {}
        for key, label in _INT_FIELDS:
            edit = QLineEdit()
            via_form.addRow(label, edit)
            self._int_edits[key] = edit

        self._tabs.addTab(files_page, _("Files"))
        self._tabs.addTab(schematics_page, _("Schematics"))
        self._tabs.addTab(via_page, _("Via"))

        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        layout.addWidget(self.save_button)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

    # ── Wiring from the Config tree ─────────────────────────────────────

    def set_target_file(self, path: Optional[Path]) -> None:
        """Called whenever the project's root file changes (wired to
        ConfigTreeDock's root_file_changed signal — see this module's
        docstring for why it's root_file_changed and not file_selected)."""
        self._show_message("")
        self._path = path
        if path is None:
            self.target_label.setText(_("No project file open"))
            self._present_keys = set()
            self._populate({})
            return
        self.target_label.setText(display_path(path))
        data = yaml_io.load_data(path)
        self._present_keys = set(data.keys())
        self._populate(data)

    def _populate(self, data: dict) -> None:
        self.layer_combo.setCurrentText(data.get("layer", _DEFAULTS["layer"]))
        for key, _label, _kind, _group in _TEXT_FIELDS:
            self._text_edits[key].setText(data.get(key) or "")
        self.schematic_files_list.clear()
        self.schematic_files_list.addItems(data.get("schematic_files") or [])
        for key, _label in _BOOL_FIELDS:
            self._bool_checks[key].setChecked(bool(data.get(key, _DEFAULTS[key])))
        for key, _label in _FLOAT_FIELDS:
            self._float_edits[key].setText(str(data.get(key, _DEFAULTS[key])))
        for key, _label in _INT_FIELDS:
            self._int_edits[key].setText(str(data.get(key, _DEFAULTS[key])))

    # ── Browse buttons for the path fields ──────────────────────────────

    def _relative_to_target(self, absolute: str) -> str:
        return Path(os.path.relpath(absolute, self._path.parent)).as_posix()

    def _browse_dir(self, edit: QLineEdit, label: str) -> None:
        if self._path is None:
            self._show_message(_("Open or create a project (root) file first."), _ERROR_STYLE)
            return
        start = (self._path.parent / edit.text()) if edit.text().strip() else self._path.parent
        chosen = QFileDialog.getExistingDirectory(self, label, str(start))
        if not chosen:
            return
        edit.setText(self._relative_to_target(chosen))

    def _browse_sch(self, edit: QLineEdit, label: str) -> None:
        if self._path is None:
            self._show_message(_("Open or create a project (root) file first."), _ERROR_STYLE)
            return
        start = (self._path.parent / edit.text()) if edit.text().strip() else self._path.parent
        chosen, _filter = QFileDialog.getOpenFileName(
            self, label, str(start), "KiCad Schematic (*.kicad_sch)")
        if not chosen:
            return
        edit.setText(self._relative_to_target(chosen))

    def _browse_file(self, edit: QLineEdit, label: str) -> None:
        if self._path is None:
            self._show_message(_("Open or create a project (root) file first."), _ERROR_STYLE)
            return
        start = (self._path.parent / edit.text()) if edit.text().strip() else self._path.parent
        chosen, _filter = QFileDialog.getSaveFileName(self, label, str(start))
        if not chosen:
            return
        edit.setText(self._relative_to_target(chosen))

    # ── Add/Remove for the schematic_files list ─────────────────────────

    def _add_schematic_file(self) -> None:
        if self._path is None:
            self._show_message(_("Open or create a project (root) file first."), _ERROR_STYLE)
            return
        chosen, _filter = QFileDialog.getOpenFileName(
            self, _("Add schematic file"), str(self._path.parent),
            "KiCad Schematic (*.kicad_sch);;All files (*)")
        if not chosen:
            return
        rel = self._relative_to_target(chosen)
        if not self.schematic_files_list.findItems(rel, Qt.MatchFlag.MatchExactly):
            self.schematic_files_list.addItem(rel)

    def _remove_schematic_file(self) -> None:
        for item in self.schematic_files_list.selectedItems():
            self.schematic_files_list.takeItem(self.schematic_files_list.row(item))

    # ── Save ──────────────────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(self.message_label, text, style, logger)

    def _on_save(self) -> None:
        if self._path is None:
            self._show_message(_("Open or create a project (root) file first."), _ERROR_STYLE)
            return

        updates: Dict[str, object] = {}

        layer = self.layer_combo.currentText()
        if layer != _DEFAULTS["layer"] or "layer" in self._present_keys:
            updates["layer"] = layer

        for key, _label, _kind, _group in _TEXT_FIELDS:
            text = self._text_edits[key].text().strip()
            if text or key in self._present_keys:
                updates[key] = text or None

        files = [self.schematic_files_list.item(i).text()
                 for i in range(self.schematic_files_list.count())]
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
