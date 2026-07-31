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
        reconnect_button = QPushButton(_("Reconnect"))
        reconnect_button.clicked.connect(self._poll)
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(reconnect_button)

        self.tree_dock = RoleClusterTreeDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_INTERVAL_MS)
        self._poll()  # don't wait a full interval for the first attempt

    def _poll(self) -> None:
        if not self.connection.is_connected:
            error = self.connection.connect()
        else:
            error = self.connection.refresh()

        if error:
            self.status_label.setText(_("Not connected: {error}").format(error=error))
            self.tree_dock.set_footprints([])
            return

        snapshot = self.connection.board.select()
        self.status_label.setText(_("Connected — {count} components").format(count=len(snapshot)))
        self.tree_dock.set_footprints(snapshot)
