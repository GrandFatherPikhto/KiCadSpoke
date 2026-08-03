# gui/docks/config_tree.py
"""
ConfigTreeDock — one tree mirroring the actual include: file graph from a
single root config file (2026-08-03, GUI tree roadmap Этап 1/2 — see
techdocs/handoff/handoff_2026_08_03_gui_tree_risks_resolved.md and the
2026-08-03 config-architecture-brainstorm session). Root = the ONE file
carrying metadata. Picked via "Open Root file..." (a plain QFileDialog, no
directory browser) + a Recent dropdown — replaces FilePickerDock entirely
(removed same day: "Да не хочу я файл-пикер"), which used to offer a
QFileSystemModel directory browser plus three independent "role" slots
(Cells/Extractor/Placer) other docks read their target file from.

Each file node shows its own directly declared entities (grouped by
section) as leaves, plus its own include: children as nested file nodes,
recursively — built via kicadstamp.config.includes.walk_include_tree(),
NOT resolve_includes() (which merges and loses file boundaries — the wrong
shape here: an earlier version of this dock read one flat file per role
and didn't walk include: at all, corrected same day, see the handoff
above).

4 of the 7 recognized sections route into an existing form when clicked
(Cells -> PlacerDock.set_selected_cell, Clone placements ->
PlacerDock.load_placement, Extract profiles -> ExtractDock.pick_profile,
Thermal via arrays -> ThermalViaArrayDock.load_entry, added 2026-08-03) —
Rules/Points/Clone profiles have no GUI edit form yet, shown read-only for
now, same deliberate scope limit as before. Rules/ManualSpoke specifically
was flagged live as a related future gap (Denis, re: fpga_cap_pair_spoke.yaml
being used via rules: spokes, not clone_placements:) — parked alongside it,
not started.

Every click (file header, category, or leaf alike) also fires
file_selected with that item's nearest file ancestor — this REPLACES the
three independent Cells/Extractor/Placer role signals FilePickerDock used
to fire: ExtractDock now always targets "whatever file is currently being
browsed in this tree" for both its Cells output and its extract_profiles
output (collapsing what used to be two independently-assignable roles
into one — Denis: "Экстракторы у нас уже автоматизированы", nothing extra
needed since extracting into a file already positioned in the tree means
it's already reachable from root, no separate include: wiring step
required in the common case).

Right-click context menu (2026-08-03): the SAME set of file-level actions
(Add cell/thermal via pad/placer/included file, Remove this file) is
offered no matter which specific item under a file you right-click — the
file/category/leaf distinction only matters for left-click routing above,
not for these actions, which always operate on "the nearest file
ancestor" (Denis: "Если выбран файл или его десцендант..." — the
descendant doesn't change WHICH file the action targets).
"""
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QInputDialog,
                              QLabel, QMenu, QMessageBox, QPushButton, QTreeWidget,
                              QTreeWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.config.includes import IncludeTreeNode, walk_include_tree
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from .. import settings
from ._common import (add_include, disable_include, display_path, merge_write,
                      non_includable_keys)

# Recent root files, most-recent-first, capped at this many entries — same
# "remember a handful of recently used paths" idea FilePickerDock's
# last_picked_path had, just for the root file specifically and with more
# than one remembered (Этап 2 roadmap: "список Recent (не только
# последний открытый») — потому что на реальной плате несколько
# независимых root-файлов, между которыми реально переключаются").
_RECENT_LIMIT = 10

# Display label per recognized section, in the order shown under a file
# node. Order matches config/includes.py's _LIST_SECTIONS + _DICT_SECTIONS.
_SECTION_LABELS = {
    "rules": _("Rules"),
    "clone_placements": _("Clone placements"),
    "thermal_via_arrays": _("Thermal via arrays"),
    "cells": _("Cells"),
    "points": _("Points"),
    "extract_profiles": _("Extract profiles"),
    "clone_profiles": _("Clone profiles"),
}

# Sections with a real leaf-click destination (see module docstring) — the
# rest are listed but inert (no existing form to route into yet).
_CLICKABLE_SECTIONS = {"cells", "clone_placements", "extract_profiles", "thermal_via_arrays"}


class ConfigTreeDock(QDockWidget):
    # Fired when a Cell leaf is clicked — PlacerDock listens to fill its
    # Cell field (see gui/dock_hub.py).
    cell_picked = pyqtSignal(str)
    # Fired when a Clone placement leaf is clicked — PlacerDock listens to
    # load it back into the form.
    placement_picked = pyqtSignal(object)
    # Fired when an Extract profile leaf is clicked — ExtractDock listens
    # via its pick_profile() entry point.
    profile_picked = pyqtSignal(str)
    # Fired when a Thermal via array leaf is clicked (2026-08-03) —
    # ThermalViaArrayDock listens to load it back into the form, same shape
    # as placement_picked.
    thermal_via_picked = pyqtSignal(object)
    # Fired by the context menu's "Add placer..." — PlacerDock listens via
    # its new_placement() entry point (opens the form blank rather than
    # writing a raw stub straight to YAML).
    add_placer_requested = pyqtSignal(object)
    # Fired by the context menu's "Add thermal via pad..." (2026-08-03,
    # replaces writing a raw {"name": ...} stub straight to YAML) —
    # ThermalViaArrayDock listens via its new_thermal_via() entry point,
    # same reasoning as add_placer_requested above.
    add_thermal_via_requested = pyqtSignal(object)
    # Fired on EVERY click in the tree (file header, category, or leaf) —
    # see module docstring for why this replaces the three independent
    # FilePickerDock role signals.
    file_selected = pyqtSignal(object)

    def __init__(self, main_window):
        super().__init__(_("Config"), main_window)
        self._main_window = main_window
        self._root_path: Optional[Path] = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        open_row = QHBoxLayout()
        open_button = QPushButton(_("Open Root file..."))
        open_button.clicked.connect(self._on_open_root)
        open_row.addWidget(open_button)
        self.recent_combo = QComboBox()
        self.recent_combo.setPlaceholderText(_("Recent..."))
        self.recent_combo.activated.connect(self._on_recent_selected)
        open_row.addWidget(self.recent_combo, 1)
        layout.addLayout(open_row)

        self.root_label = QLabel(_("No root file open"))
        self.root_label.setWordWrap(True)
        layout.addWidget(self.root_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.tree)

        self.setWidget(container)

        self._reload_recent_combo()
        self._restore_last_root()

    # ── Opening a root file (replaces FilePickerDock entirely) ─────────

    def _on_open_root(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self, _("Open Root file"), str(self._root_path.parent if self._root_path else ""),
            "YAML files (*.yaml *.yml)")
        if not chosen:
            return
        self.set_root_file(Path(chosen))

    def _on_recent_selected(self, index: int) -> None:
        path_str = self.recent_combo.itemData(index)
        if path_str:
            self.set_root_file(Path(path_str))

    def _remember_recent(self, path: Path) -> None:
        recent = [p for p in settings.state.get("recent_root_files", []) if p != str(path)]
        recent.insert(0, str(path))
        settings.state.set("recent_root_files", recent[:_RECENT_LIMIT])
        settings.state.set("last_root_file", str(path))
        self._reload_recent_combo()

    def _reload_recent_combo(self) -> None:
        self.recent_combo.blockSignals(True)
        self.recent_combo.clear()
        for path_str in settings.state.get("recent_root_files", []):
            self.recent_combo.addItem(display_path(Path(path_str)), path_str)
        self.recent_combo.setCurrentIndex(-1)
        self.recent_combo.blockSignals(False)

    def _restore_last_root(self) -> None:
        last = settings.state.get("last_root_file")
        if last and Path(last).is_file():
            self.set_root_file(Path(last))

    # ── Setting/refreshing the root ─────────────────────────────────────

    def set_root_file(self, path: Optional[Path]) -> None:
        self._root_path = path
        if path is not None:
            self.root_label.setText(display_path(path))
            self._remember_recent(path)
        else:
            self.root_label.setText(_("No root file open"))
        self.refresh()

    def refresh(self) -> None:
        """Public — also called by PlacerDock's saved signal (see
        gui/dock_hub.py) so a successful Save shows up here without
        reassigning the root file."""
        self.tree.clear()
        if self._root_path is None:
            return
        try:
            node = walk_include_tree(str(self._root_path))
        except ValidationError as e:
            QTreeWidgetItem(self.tree, [str(e)])
            return
        self._build_file_item(self.tree.invisibleRootItem(), node, parent_path=None)
        self.tree.expandAll()

    # ── Building the tree from an IncludeTreeNode ───────────────────────

    def _build_file_item(self, parent_item, node: IncludeTreeNode,
                          parent_path: Optional[Path]) -> None:
        file_item = QTreeWidgetItem(parent_item, [node.path.name])
        file_item.setData(0, Qt.ItemDataRole.UserRole, ("file", node.path, parent_path))
        for section, label in _SECTION_LABELS.items():
            raw = node.sections.get(section)
            if not raw:
                continue
            section_item = QTreeWidgetItem(file_item, [label])
            for name, payload in self._entries(raw):
                leaf = QTreeWidgetItem(section_item, [name])
                if section in _CLICKABLE_SECTIONS:
                    leaf.setData(0, Qt.ItemDataRole.UserRole, ("leaf", section, payload))
        for child in node.children:
            self._build_file_item(file_item, child, parent_path=node.path)

    @staticmethod
    def _entries(raw):
        """Yields (display name, click payload), sorted by name. Dict
        sections (cells/extract_profiles/...) are keyed by name — the
        payload is the name itself. List sections (rules/clone_placements/
        thermal_via_arrays) carry their own name field — the payload is
        the whole entry, needed by placement_picked (load_placement wants
        the full dict, not just the name). rules: entries may omit name:
        entirely (same "name or net" fallback as config/models.py's
        rule_effective_name() — name is only needed to give a rule a more
        readable label than its net, not a requirement)."""
        if isinstance(raw, dict):
            for name in sorted(raw.keys()):
                yield name, name
            return
        named = []
        for e in raw:
            if not isinstance(e, dict):
                continue
            display_name = e.get("name") or e.get("net")
            if display_name:
                named.append((display_name, e))
        for display_name, entry in sorted(named, key=lambda pair: pair[0]):
            yield display_name, entry

    # ── Click routing (left-click anywhere in the tree) ─────────────────

    def _on_clicked(self, item, column) -> None:
        file_ctx = self._file_context_for_item(item)
        if file_ctx is not None:
            self.file_selected.emit(file_ctx[0])

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None or data[0] != "leaf":
            return  # clicked a file/category header, not a leaf
        _kind, section, ref = data
        if section == "cells":
            self.cell_picked.emit(ref)
        elif section == "clone_placements":
            self.placement_picked.emit(ref)
        elif section == "extract_profiles":
            self.profile_picked.emit(ref)
        elif section == "thermal_via_arrays":
            self.thermal_via_picked.emit(ref)

    # ── Context menu (right-click anywhere under a file) ────────────────

    def _file_context_for_item(self, item) -> Optional[tuple]:
        """Walks up from `item` (inclusive) to the nearest file node —
        every action below operates on that file regardless of whether the
        file header itself, a category, or a leaf was actually clicked."""
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data is not None and data[0] == "file":
                return data[1], data[2]  # (file_path, parent_path)
            item = item.parent()
        return None

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        file_ctx = self._file_context_for_item(item)
        if file_ctx is None:
            return
        file_path, parent_path = file_ctx

        menu = QMenu(self.tree)
        menu.addAction(_("Add cell...")).triggered.connect(
            lambda: self._add_cell(file_path))
        menu.addAction(_("Add thermal via pad...")).triggered.connect(
            lambda: self.add_thermal_via_requested.emit(file_path))
        menu.addAction(_("Add placer...")).triggered.connect(
            lambda: self.add_placer_requested.emit(file_path))
        menu.addAction(_("Add included file...")).triggered.connect(
            lambda: self._add_included_file(file_path))
        if parent_path is not None:
            menu.addSeparator()
            menu.addAction(_("Remove this file")).triggered.connect(
                lambda: self._remove_file(file_path, parent_path))

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _add_cell(self, file_path: Path) -> None:
        name, ok = QInputDialog.getText(self, _("Add cell"), _("Cell name:"))
        name = name.strip()
        if not ok or not name:
            return
        merge_write(file_path, {"cells": {name: {"components": []}}}, section="cells")
        self.refresh()

    def _add_included_file(self, file_path: Path) -> None:
        """QFileDialog's SAVE mode (not Open) is used deliberately — it
        lets the user type a filename that doesn't exist yet, per Denis:
        "если включаем файл, его может реально и не быть". The dialog
        itself never touches disk; if the chosen path doesn't exist, an
        empty file is created here before wiring include:."""
        chosen, _filter = QFileDialog.getSaveFileName(
            self, _("Add included file"), str(file_path.parent), "YAML (*.yaml)")
        if not chosen:
            return
        chosen_path = Path(chosen)
        if not chosen_path.exists():
            chosen_path.write_text("{}\n", encoding="utf-8")

        bad_keys = non_includable_keys(chosen_path)
        if bad_keys:
            QMessageBox.warning(
                self, _("Cannot include"),
                _("{name} has root-config-only key(s) {keys} that include: can't merge — "
                  "move them out, or point Root at this file directly instead.")
                .format(name=chosen_path.name, keys=sorted(bad_keys)))
            return

        rel = Path(os.path.relpath(chosen_path, file_path.parent)).as_posix()
        add_include(file_path, rel)
        self.refresh()

    def _remove_file(self, file_path: Path, parent_path: Path) -> None:
        reply = QMessageBox.question(
            self, _("Remove file"),
            _("Remove {name!r} from {parent!r}'s include:? The file itself is not deleted — "
              "this can be undone later by adding it again.")
            .format(name=file_path.name, parent=parent_path.name))
        if reply != QMessageBox.StandardButton.Yes:
            return
        disable_include(parent_path, file_path)
        self.refresh()
