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
directly). Wiring the refresh callback here (main_window.tree_dock is
constructed before this dock, confirmed by gui/main_window.py's ordering)
is what keeps that view in sync with an explicit Rescan/Apply in this tab.
"""
from PyQt6.QtWidgets import QDockWidget

from fieldstool.gui.main_window import MainWindow as FieldsToolMainWindow
from kicadstamp.i18n import _


class FieldsToolDock(QDockWidget):
    def __init__(self, main_window, timeout_ms: int):
        super().__init__(_("fieldstool"), main_window)
        self.window = FieldsToolMainWindow(timeout_ms=timeout_ms)
        self.window.on_components_changed = main_window.tree_dock.refresh_schematic_view
        self.setWidget(self.window)
