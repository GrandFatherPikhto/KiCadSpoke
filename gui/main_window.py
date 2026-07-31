# gui/main_window.py
"""
MainWindow — persistent shell for the KiCadStamp GUI: connection lifecycle
+ status bar + one dock (Role/Cluster tree) for now.

Step 1 of the planned GUI (see techdocs/handoff for the design discussion):
deliberately just this one vertical slice — connect/reconnect, poll, show
the live snapshot grouped by Role/Cluster, click to highlight on the real
board — before adding the bulk Role/Cluster field editor or the
extract-to-file dock. kipy 0.7.1's Board has no selection/board-change push
events (checked directly against the installed kipy.board.Board class), so
"live" here means polled on a QTimer, not pushed.

The timer's automatic tick only ever tries to CONNECT (while disconnected)
— it deliberately never re-fetches/rebuilds the tree on its own. An earlier
version also auto-refreshed every tick while connected, which rebuilds
RoleClusterTreeDock's whole QStandardItemModel each time; even with
selection/expansion restored, the visible flash/scroll-jump on an idle,
unchanged board was distracting (reported live 2026-08-01). Re-fetching the
snapshot and rebuilding the tree now only happens on an explicit action —
the status-bar button (Reconnect while disconnected, Refresh while
connected) — a deliberate user action, not a timer tick.
"""
import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel, QMainWindow, QPushButton

from kicadstamp.i18n import _

from .connection import BoardConnection
from .docks.role_cluster_tree import RoleClusterTreeDock

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 2000


class MainWindow(QMainWindow):
    def __init__(self, timeout_ms: int):
        super().__init__()
        self.setWindowTitle(_("KiCadStamp"))
        self.resize(360, 640)

        self.connection = BoardConnection(timeout_ms=timeout_ms)

        self.status_label = QLabel(_("Not connected"))
        self.action_button = QPushButton(_("Reconnect"))
        self.action_button.clicked.connect(lambda: self._poll(manual=True))
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.action_button)

        self.tree_dock = RoleClusterTreeDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_INTERVAL_MS)
        self._poll(manual=True)  # don't wait a full interval for the first attempt

    def _poll(self, manual: bool = False) -> None:
        """manual=True (button click, or the initial call at startup) always
        does real work. manual=False (an automatic timer tick) only tries to
        connect while disconnected — see module docstring for why an
        already-connected idle tick is a deliberate no-op."""
        if self.connection.is_connected:
            if not manual:
                return
            error = self.connection.refresh()
        else:
            error = self.connection.connect()

        if error:
            self.status_label.setText(_("Not connected: {error}").format(error=error))
            self.tree_dock.set_footprints([])
        else:
            snapshot = self.connection.board.select()
            self.status_label.setText(_("Connected — {count} components").format(count=len(snapshot)))
            self.tree_dock.set_footprints(snapshot)

        self.action_button.setText(_("Refresh") if self.connection.is_connected else _("Reconnect"))
