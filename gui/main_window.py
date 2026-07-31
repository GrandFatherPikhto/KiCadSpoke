# gui/main_window.py
"""
MainWindow — persistent shell for the KiCadStamp GUI: connection lifecycle
+ status bar + docks (Role/Cluster tree, bulk Role/Cluster field editor,
file picker, extract-to-file).

Step 1: RoleClusterTreeDock — connect/reconnect, poll, show the live
snapshot grouped by Role/Cluster, click to highlight on the real board.
Step 2: BulkFieldEditorDock, the first real mutating panel — set Role/
Cluster on whatever's currently selected. Then FilePickerDock (pick a
target file by clicking instead of typing a path) and ExtractDock (build a
Cell from the current selection, write it into that target file). kipy
0.7.1's Board has no selection/board-change push events (checked directly
against the installed kipy.board.Board class), so "live" here means polled
on a QTimer, not pushed.

The timer's automatic tick only ever tries to CONNECT (while disconnected)
— it deliberately never re-fetches/rebuilds the tree on its own. An earlier
version also auto-refreshed every tick while connected, which rebuilds
RoleClusterTreeDock's whole QStandardItemModel each time; even with
selection/expansion restored, the visible flash/scroll-jump on an idle,
unchanged board was distracting (reported live 2026-08-01). Re-fetching the
snapshot and rebuilding the tree now only happens on an explicit action —
the status-bar button (Reconnect while disconnected, Refresh while
connected) — a deliberate user action, not a timer tick.

A SEPARATE, faster timer watches the board's own GUI selection (board ->
tree, the reverse of clicking a tree node) so re-selecting something by
mouse in KiCad shows up in the tree too. Deliberately still a QTimer on the
same (main/UI) thread, NOT a background QThread: kipy's connection is a
plain pynng.Req0 (request/reply) socket with no locking anywhere in
kipy/client.py — a REQ socket only ever has one request in flight, so a
second thread calling into the same KiCadBoardAdapter concurrently with the
main thread (e.g. a "Refresh" click landing mid-poll) would race on that one
socket. get_selected_items() is cheap enough (one get_selection() round
trip against the already-cached footprint list, no per-footprint Role/
Cluster field reads) that a short interval here doesn't need a thread.
"""
import logging

from kipy.board_types import FootprintInstance
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QCheckBox, QLabel, QMainWindow, QPushButton

from kicadstamp.i18n import _

from . import settings
from .connection import BoardConnection
from .docks.bulk_field_editor import BulkFieldEditorDock
from .docks.extract import ExtractDock
from .docks.file_picker import FilePickerDock
from .docks.role_cluster_tree import RoleClusterTreeDock

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 2000
SELECTION_POLL_INTERVAL_MS = 400


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

        self.always_on_top_checkbox = QCheckBox(_("Always on top"))
        self.always_on_top_checkbox.toggled.connect(self._set_always_on_top)
        self.statusBar().addPermanentWidget(self.always_on_top_checkbox)
        self.statusBar().addPermanentWidget(self.action_button)

        self.tree_dock = RoleClusterTreeDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)

        self.bulk_edit_dock = BulkFieldEditorDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.bulk_edit_dock)

        self.file_picker_dock = FilePickerDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.file_picker_dock)
        self.tabifyDockWidget(self.bulk_edit_dock, self.file_picker_dock)

        self.extract_dock = ExtractDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.extract_dock)
        self.tabifyDockWidget(self.file_picker_dock, self.extract_dock)

        # Files -> Extract wiring: ExtractDock's cell-output file follows
        # the Cells role, its extract_profiles file follows the Extractor
        # role (both assigned via "Use selected" in the Files dock).
        # _restore_roles() (inside FilePickerDock's own __init__, already
        # ran) may have already restored a role from a previous session
        # before these callbacks existed to hear about it — push the
        # current values once explicitly so a restored assignment isn't
        # silently missed.
        self.file_picker_dock.on_cells_file_changed = self.extract_dock.set_target_file
        self.file_picker_dock.on_extractor_file_changed = self.extract_dock.set_profile_file
        self.extract_dock.set_target_file(self.file_picker_dock.assigned["cells"])
        self.extract_dock.set_profile_file(self.file_picker_dock.assigned["extractor"])

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_INTERVAL_MS)

        self._selection_timer = QTimer(self)
        self._selection_timer.timeout.connect(self._poll_board_selection)
        self._selection_timer.start(SELECTION_POLL_INTERVAL_MS)

        self._restore_window_state()

        self._poll(manual=True)  # don't wait a full interval for the first attempt

    def _restore_window_state(self) -> None:
        """Plain x/y/width/height ints in gui_state.json, not Qt's own
        saveGeometry()/restoreGeometry() (a QByteArray blob — would need
        base64 to fit in JSON at all) or QSettings — same reason the rest of
        this GUI's persistence is plain JSON: staying human-readable/
        inspectable in one place beats using the platform-native mechanism
        for just this one thing."""
        data = settings.load()
        geometry = data.get("window_geometry")
        if geometry and all(k in geometry for k in ("x", "y", "width", "height")):
            self.setGeometry(geometry["x"], geometry["y"], geometry["width"], geometry["height"])
        if data.get("always_on_top"):
            self.always_on_top_checkbox.setChecked(True)  # triggers _set_always_on_top via its signal

    def closeEvent(self, event) -> None:
        rect = self.geometry()
        data = settings.load()
        data["window_geometry"] = {"x": rect.x(), "y": rect.y(),
                                    "width": rect.width(), "height": rect.height()}
        data["always_on_top"] = self.always_on_top_checkbox.isChecked()
        settings.save(data)
        super().closeEvent(event)

    def _set_always_on_top(self, checked: bool) -> None:
        """setWindowFlag() only takes effect on the next show() — the window
        briefly disappears and reappears on most platforms (X11/Windows),
        which is the normal/expected way Qt does this, not a bug here."""
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()

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
            self.bulk_edit_dock.refresh_known_values(self.connection.board)

        self.action_button.setText(_("Refresh") if self.connection.is_connected else _("Reconnect"))

    def _poll_board_selection(self) -> None:
        """The fast timer's tick — see module docstring. Failure here (most
        likely: KiCad closed between two _poll() ticks, since that one only
        re-verifies the connection every POLL_INTERVAL_MS) is treated as a
        connection loss: update the status bar immediately rather than
        waiting for the slower timer to notice, but don't touch the tree's
        component list itself — only its live-selection highlighting."""
        if not self.connection.is_connected:
            return
        try:
            items = self.connection.board.adapter.get_selected_items()
        except Exception as e:
            logger.warning("Lost connection while polling board selection: %s", e)
            self.connection.board = None
            self.status_label.setText(_("Not connected: {error}").format(error=str(e)))
            self.action_button.setText(_("Reconnect"))
            return
        refs = {item.reference_field.text.value for item in items
                if isinstance(item, FootprintInstance)}
        self.tree_dock.highlight_board_selection(refs)

        by_ref = {s.ref: s for s in self.connection.board.select()}
        selected = [by_ref[ref] for ref in refs if ref in by_ref]
        self.bulk_edit_dock.set_board_selection(selected)
        self.extract_dock.set_board_selection(items, selected)
