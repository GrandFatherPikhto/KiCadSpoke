# gui/docks/fieldstool_dock.py
"""
FieldsToolDock — embeds fieldstool.gui.main_window.MainWindow (fieldstool's
own standalone QMainWindow, with its own internal PendingChangesDock)
whole, as-is, inside one QDockWidget via setWidget(). QDockWidget.setWidget()
accepts any QWidget and QMainWindow is one, so fieldstool's own internal
docking keeps working nested here exactly like it does standalone
(fieldstool_gui.py) — no restructuring of fieldstool/gui/ needed, it stays
dependency-free of gui/.

Phase 5.1 (gui-optimization roadmap): this dock embeds fieldstool's window
with the main GUI's OWN BoardConnection, and the embedded window does NOT
start its own two QTimers in that case — kipy's REQ socket allows exactly
one request in flight, so two timers driving the same connection would
interleave requests mid-flight. One connection, one polling loop: the main
GUI's single 2s/400ms poll feeds the embedded window through
push_live_selection()/set_connection_status(). Standalone
(fieldstool_gui.py) passes no connection and keeps its own timers.

fieldstool's own Components tree (fieldstool/gui/tree.py) was retired
2026-08-01 — the main GUI's own Components tree (gui/docks/
role_cluster_tree.py) covers the same job in its "Not yet applied" mode
when embedded here (reading this window's parsed schematic components
directly). The refresh callback that keeps that view in sync with an
explicit Rescan/Apply in this tab is wired through this dock's
components_changed signal (connected in gui/main_window.py, the composition
root) — this dock never reaches back into main_window.tree_dock itself.
"""
from typing import List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDockWidget

from fieldstool.gui.main_window import MainWindow as FieldsToolMainWindow
from kicadstamp.i18n import _


class FieldsToolDock(QDockWidget):
    # Fired when fieldstool's window rescans the schematic (its
    # on_components_changed hook) — the main GUI's Components tree listens
    # to refresh its "Not yet applied" view (see gui/main_window.py).
    components_changed = pyqtSignal()

    def __init__(self, main_window, timeout_ms: int, connection=None):
        super().__init__(_("fieldstool"), main_window)
        # Phase 5.1 — embed with the main GUI's OWN BoardConnection (one
        # kipy client, one REQ socket): one connection + one polling loop
        # instead of two independent ones. The embedded window stops its own
        # timers in that case (see fieldstool/gui/main_window.py); standalone
        # fieldstool_gui.py keeps its own connection and timers.
        self.window = FieldsToolMainWindow(timeout_ms=timeout_ms, connection=connection)
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

    def push_live_selection(self, refs: List[str]) -> None:
        """Route the main GUI's single 400ms live-selection tick into the
        embedded window (Phase 5.1 — its own selection timer is stopped when
        it shares the main connection, so this is now the only path)."""
        self.window.set_live_selection(refs)

    def set_connection_status(self, error: Optional[str]) -> None:
        """Mirror the shared connection's state into the embedded window's
        status label (Phase 5.1 — its own connect/refresh poll is stopped)."""
        self.window.set_connection_status(error)
