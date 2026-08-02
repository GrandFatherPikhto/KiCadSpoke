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
"""
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDockWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from kicadstamp.config.includes import IncludeTreeNode, walk_include_tree
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

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
        self._build_file_item(self.tree.invisibleRootItem(), node)
        self.tree.expandAll()

    # ── Building the tree from an IncludeTreeNode ───────────────────────

    def _build_file_item(self, parent_item, node: IncludeTreeNode) -> None:
        file_item = QTreeWidgetItem(parent_item, [node.path.name])
        for section, label in _SECTION_LABELS.items():
            raw = node.sections.get(section)
            if not raw:
                continue
            section_item = QTreeWidgetItem(file_item, [label])
            for name, payload in self._entries(raw):
                leaf = QTreeWidgetItem(section_item, [name])
                if section in _CLICKABLE_SECTIONS:
                    leaf.setData(0, Qt.ItemDataRole.UserRole, (section, payload))
        for child in node.children:
            self._build_file_item(file_item, child)

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

    # ── Click routing ────────────────────────────────────────────────────

    def _on_clicked(self, item, column) -> None:
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if payload is None:
            return  # clicked a file/category header, not a leaf
        section, ref = payload
        if section == "cells":
            self.cell_picked.emit(ref)
        elif section == "clone_placements":
            self.placement_picked.emit(ref)
        elif section == "extract_profiles":
            self.profile_picked.emit(ref)
