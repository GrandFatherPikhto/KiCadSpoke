# gui/docks/placer_list.py
"""
PlacerListDock — list of clone_placements: entries already saved in the
Placer file, tabified with the Components tree and the Cells list.
Clicking an entry loads it back into PlacerDock's form (load_placement())
for editing/Redraw — same "pick from a list you already browse" pattern
CellListDock/RoleClusterTreeDock use for Cell/Cluster. Requested live
2026-08-02: "таб пласеров (там где дерево компонент и экстракторов)".

refresh() is called both on set_placer_file() (file picked/changed) and
via PlacerDock.on_saved (a Save just added/overwrote an entry) — the list
would otherwise go stale the moment the user Saves without reassigning
the Placer file (see gui/main_window.py wiring).
"""
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtWidgets import QDockWidget, QListWidget, QVBoxLayout, QWidget

from kicadstamp.i18n import _

from .. import yaml_io


class PlacerListDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__(_("Placements"), main_window)
        self._main_window = main_window
        self._placer_path: Optional[Path] = None
        self.on_placement_picked: Optional[Callable[[dict], None]] = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.list = QListWidget()
        self.list.itemClicked.connect(self._on_clicked)
        layout.addWidget(self.list)

        self.setWidget(container)

    def set_placer_file(self, path: Optional[Path]) -> None:
        self._placer_path = path
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        names = sorted(e["name"] for e in self._entries() if e.get("name"))
        self.list.addItems(names)

    def _entries(self) -> list:
        data = yaml_io.load_data(self._placer_path).get("clone_placements") or []
        return [e for e in data if isinstance(e, dict)]

    def _on_clicked(self, item) -> None:
        if self.on_placement_picked is None:
            return
        for entry in self._entries():
            if entry.get("name") == item.text():
                self.on_placement_picked(entry)
                return
