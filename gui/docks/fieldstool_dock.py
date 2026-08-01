# gui/docks/fieldstool_dock.py
"""
FieldsToolDock — embeds fieldstool.gui.main_window.MainWindow (fieldstool's
own standalone QMainWindow, with its own internal PendingChangesDock)
whole, as-is, inside one QDockWidget via setWidget(). QDockWidget.setWidget()
accepts any QWidget and QMainWindow is one, so fieldstool's own internal
docking keeps working nested here exactly like it does standalone
(fieldstool_gui.py) — no restructuring of fieldstool/gui/ needed, it stays
dependency-free of gui/.

fieldstool's MainWindow starts its own two QTimers (poll + selection-watch)
unconditionally in __init__, so its BoardConnection begins polling KiCad
immediately once this dock is constructed — independent of and no less safe
than kicadstamp_gui's own connection (see kicadstamp/kicad/adapter.py: no
shared/global state, a fresh kipy.KiCad client per Board.connect() call).

fieldstool's own Components tree (fieldstool/gui/tree.py) was retired
2026-08-01 — the main GUI's own Components tree (gui/docks/
role_cluster_tree.py) covers the same job in its "Not yet applied" mode
when embedded here (reading this window's parsed schematic components
directly). The refresh callback that keeps that view in sync with an
explicit Rescan/Apply in this tab is wired through this dock's
components_changed signal (connected in gui/main_window.py, the composition
root) — this dock never reaches back into main_window.tree_dock itself.
"""
from typing import List

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDockWidget

from fieldstool.gui.main_window import MainWindow as FieldsToolMainWindow
from kicadstamp.i18n import _


class FieldsToolDock(QDockWidget):
    # Fired when fieldstool's window rescans the schematic (its
    # on_components_changed hook) — the main GUI's Components tree listens
    # to refresh its "Not yet applied" view (see gui/main_window.py).
    components_changed = pyqtSignal()

    def __init__(self, main_window, timeout_ms: int):
        super().__init__(_("fieldstool"), main_window)
        self.window = FieldsToolMainWindow(timeout_ms=timeout_ms)
        self.window.on_components_changed = self.components_changed.emit
        self.setWidget(self.window)

    @property
    def components(self):
        """Public read-only access to fieldstool's parsed-schematic list —
        delegates to the embedded window's own public property, so the main
        GUI's tree never touches the private `_components`."""
        return self.window.components

    def pick_group(self, field: str, value: str, refs: List[str]) -> None:
        """Route a group-node click from the main GUI's Components tree into
        fieldstool's existing _on_group_picked() staging/combo-fill logic."""
        self.window._on_group_picked(field, value, refs)

    def pick_leaf(self, refs: List[str]) -> None:
        """Route a leaf-node click from the main GUI's Components tree into
        fieldstool's existing _on_tree_leaf_picked() logic."""
        self.window._on_tree_leaf_picked(refs)
