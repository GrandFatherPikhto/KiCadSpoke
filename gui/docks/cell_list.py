# gui/docks/cell_list.py
"""
CellListDock — a plain list of Cell names read from the Files dock's
Cells role, tabified with the Components tree (RoleClusterTreeDock) on
the left. Requested live 2026-08-01: PlacerDock originally had its own
embedded Cell list, but it wasn't where the user expected to find it —
"где выбирать cell? Думаю, к дереву компонент надо добавить табик со
списком cell" — cell picking now lives alongside the tree the user
already browses, and PlacerDock just listens (on_cell_picked), same
pattern RoleClusterTreeDock's on_cluster_picked already uses for the
Cluster field.
"""
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtWidgets import QDockWidget, QListWidget, QVBoxLayout, QWidget

from kicadstamp.i18n import _

from .. import yaml_io


class CellListDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__(_("Cells"), main_window)
        self._main_window = main_window
        self._cells_path: Optional[Path] = None
        self.on_cell_picked: Optional[Callable[[str], None]] = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.list = QListWidget()
        self.list.itemClicked.connect(self._on_clicked)
        layout.addWidget(self.list)

        self.setWidget(container)

    def set_cells_file(self, path: Optional[Path]) -> None:
        self._cells_path = path
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        self.list.addItems(sorted(yaml_io.existing_keys(self._cells_path)))

    def _on_clicked(self, item) -> None:
        if self.on_cell_picked is not None:
            self.on_cell_picked(item.text())
