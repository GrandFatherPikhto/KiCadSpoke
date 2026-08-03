# gui/docks/config_tree.py
"""
ConfigTreeDock — one tree mirroring the actual include: file graph from a
single root config file (2026-08-03, GUI tree roadmap Этап 1/2 — see
techdocs/handoff/handoff_2026_08_03_gui_tree_risks_resolved.md and the
2026-08-03 config-architecture-brainstorm session). Root = the ONE file
carrying metadata (today, practically wired to the Placer role — ExtractDock
already writes Cells/Extractor files into the Placer file's own include:
after a successful extract, "placer — это точка сборки"; see set_root_file's
docstring for how this is currently bridged, pending a dedicated "Open Root
file" action that will replace FilePickerDock, Этап 2).

Each file node shows its own directly declared entities (grouped by
section) as leaves, plus its own include: children as nested file nodes,
recursively — built via kicadstamp.config.includes.walk_include_tree(),
NOT resolve_includes() (which merges and loses file boundaries — the wrong
shape here: an earlier version of this dock read one flat file per role
and didn't walk include: at all, corrected same day, see the handoff
above).

Only 3 of the 7 recognized sections route into an existing form when
clicked (Cells -> PlacerDock.set_selected_cell, Clone placements ->
PlacerDock.load_placement, Extract profiles -> ExtractDock.pick_profile) —
Rules/Thermal via arrays/Points/Clone profiles have no GUI edit form yet,
shown read-only for now, same deliberate scope limit as before.

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
from PyQt6.QtWidgets import (QDockWidget, QFileDialog, QInputDialog, QMenu, QMessageBox,
                              QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.config.includes import IncludeTreeNode, walk_include_tree
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ._common import add_include, disable_include, merge_write, non_includable_keys, upsert_list_entry

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
_CLICKABLE_SECTIONS = {"cells", "clone_placements", "extract_profiles"}


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
    # Fired by the context menu's "Add placer..." — PlacerDock listens via
    # its new_placement() entry point (opens the form blank rather than
    # writing a raw stub straight to YAML).
    add_placer_requested = pyqtSignal(object)

    def __init__(self, main_window):
        super().__init__(_("Config"), main_window)
        self._main_window = main_window
        self._root_path: Optional[Path] = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.tree)

        self.setWidget(container)

    # ── Wiring from the Files dock ──────────────────────────────────────

    def set_root_file(self, path: Optional[Path]) -> None:
        """Bridged (2026-08-03) to FilePickerDock's Placer role — the
        closest existing thing to "the one root file" today, since a
        dedicated "Open Root file" action doesn't exist yet (Этап 2,
        replaces FilePickerDock entirely). Will be repointed to that
        action once built; nothing else about this dock changes when it
        is."""
        self._root_path = path
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

    # ── Click routing (left-click on a leaf) ────────────────────────────

    def _on_clicked(self, item, column) -> None:
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
            lambda: self._add_thermal_via_pad(file_path))
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

    def _add_thermal_via_pad(self, file_path: Path) -> None:
        name, ok = QInputDialog.getText(self, _("Add thermal via pad"), _("Name:"))
        name = name.strip()
        if not ok or not name:
            return
        upsert_list_entry(file_path, "thermal_via_arrays", {"name": name})
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
