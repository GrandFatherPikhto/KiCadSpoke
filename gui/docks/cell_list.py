# gui/docks/cell_list.py
"""
CellListDock — a plain list of Cell names read from the Files dock's
Cells role, tabified with the Components tree (RoleClusterTreeDock) on
the left. Requested live 2026-08-01: PlacerDock originally had its own
embedded Cell list, but it wasn't where the user expected to find it —
"где выбирать cell? Думаю, к дереву компонент надо добавить табик со
списком cell" — cell picking now lives alongside the tree the user
already browses, and PlacerDock just listens (the cell_picked signal),
same pattern RoleClusterTreeDock's cluster_picked signal already uses for
the Cluster field.
"""
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDockWidget, QListWidget, QVBoxLayout, QWidget

from kicadstamp.i18n import _

from .. import yaml_io


class CellListDock(QDockWidget):
    # Fired when a cell in the list is clicked — PlacerDock listens to fill
    # its Cell field (see gui/main_window.py).
    cell_picked = pyqtSignal(str)

    def __init__(self, main_window):
        super().__init__(_("Cells"), main_window)
        self._main_window = main_window
        self._cells_path: Optional[Path] = None

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
        self.cell_picked.emit(item.text())
