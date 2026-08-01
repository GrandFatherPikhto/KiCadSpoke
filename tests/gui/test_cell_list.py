# tests/gui/test_cell_list.py
from gui.docks.cell_list import CellListDock


def test_list_populated_from_cells_file_and_click_fires_callback(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    cells_file.write_text("pi_filter: {}\nldo_adj: {}\n", encoding="utf-8")

    dock = CellListDock(main_window)
    dock.set_cells_file(cells_file)
    assert [dock.list.item(i).text() for i in range(dock.list.count())] == ["ldo_adj", "pi_filter"]

    picked = []
    dock.on_cell_picked = picked.append
    dock.list.itemClicked.emit(dock.list.item(0))
    assert picked == ["ldo_adj"]
