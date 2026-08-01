# gui/main_window.py
"""
MainWindow — persistent shell for the KiCadStamp GUI: connection lifecycle
+ status bar + docks (Role/Cluster tree, fieldstool, file picker,
extract-to-file) + an optional tray icon.

Step 1: RoleClusterTreeDock — connect/reconnect, poll, show the live
snapshot grouped by Role/Cluster, click to highlight on the real board.
Step 2 used to be BulkFieldEditorDock, a PCB-only live-IPC Role/Cluster
editor — retired 2026-08-01: any field it wrote got silently reverted by
KiCad's own "Update PCB from Schematic" (Role/Cluster actually originate in
the schematic symbol), which is exactly the problem `fieldstool` was built
to solve correctly (direct `.kicad_sch` edits). FieldsToolDock (see
gui/docks/fieldstool_dock.py) now occupies that first right-hand tab
instead, embedding fieldstool's own standalone MainWindow whole. Then
FilePickerDock (pick a target file by clicking instead of typing a path)
and ExtractDock (build a Cell from the current selection, write it into
that target file). kipy 0.7.1's Board has no selection/board-change push
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
from typing import Optional

from kipy.board_types import FootprintInstance
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QApplication, QCheckBox, QLabel, QMainWindow,
                              QMenu, QPushButton, QSystemTrayIcon)

from kicadstamp.i18n import _

from . import settings
from .connection import BoardConnection
from .docks.cell_list import CellListDock
from .docks.extract import ExtractDock
from .docks.fieldstool_dock import FieldsToolDock
from .docks.file_picker import FilePickerDock
from .docks.log_panel import LogDock
from .docks.placer import PlacerDock
from .docks.placer_list import PlacerListDock
from .docks.role_cluster_tree import RoleClusterTreeDock
from .tray_icon import build_tray_icon

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 2000
SELECTION_POLL_INTERVAL_MS = 400


class MainWindow(QMainWindow):
    def __init__(self, timeout_ms: int, verbose: bool = False):
        super().__init__()
        self.setWindowTitle(_("KiCadStamp"))
        self.resize(360, 640)

        self.connection = BoardConnection(timeout_ms=timeout_ms)
        self._tray_icon: Optional[QSystemTrayIcon] = None

        self.status_label = QLabel(_("Not connected"))
        self.action_button = QPushButton(_("Reconnect"))
        self.action_button.clicked.connect(lambda: self._poll(manual=True))
        self.statusBar().addWidget(self.status_label, 1)

        self.always_on_top_checkbox = QCheckBox(_("Always on top"))
        self.always_on_top_checkbox.toggled.connect(self._set_always_on_top)
        self.statusBar().addPermanentWidget(self.always_on_top_checkbox)

        self.tray_checkbox = QCheckBox(_("Tray icon"))
        self.tray_checkbox.toggled.connect(self._set_tray_enabled)
        self.statusBar().addPermanentWidget(self.tray_checkbox)

        self.open_fieldstool_button = QPushButton(_("Open fieldstool"))
        self.open_fieldstool_button.clicked.connect(self.open_fieldstool)
        self.statusBar().addPermanentWidget(self.open_fieldstool_button)

        self.statusBar().addPermanentWidget(self.action_button)

        self.tree_dock = RoleClusterTreeDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)

        self.cell_list_dock = CellListDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.cell_list_dock)
        self.tabifyDockWidget(self.tree_dock, self.cell_list_dock)

        self.placer_list_dock = PlacerListDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.placer_list_dock)
        self.tabifyDockWidget(self.cell_list_dock, self.placer_list_dock)

        self.fieldstool_dock = FieldsToolDock(self, timeout_ms=timeout_ms)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.fieldstool_dock)
        # Only safe now that fieldstool_dock exists — restoring "schematic
        # mode" would otherwise touch self.fieldstool_dock before it's built
        # (see RoleClusterTreeDock.restore_mode_from_settings()'s docstring).
        self.tree_dock.restore_mode_from_settings()

        self.file_picker_dock = FilePickerDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.file_picker_dock)
        self.tabifyDockWidget(self.fieldstool_dock, self.file_picker_dock)

        self.extract_dock = ExtractDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.extract_dock)
        self.tabifyDockWidget(self.file_picker_dock, self.extract_dock)

        self.placer_dock = PlacerDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.placer_dock)
        self.tabifyDockWidget(self.extract_dock, self.placer_dock)

        self.log_dock = LogDock(self, verbose=verbose)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)

        # Files -> Extract/Placer/Cells-tab wiring: ExtractDock's cell-
        # output file, PlacerDock's placeholder discovery, and the Cells
        # tab's own list all follow the Cells role; ExtractDock's
        # extract_profiles file follows the Extractor role; ExtractDock's
        # and PlacerDock's Placer-file each follow the Placer role (both
        # assigned via "Use selected" in the Files dock — one role can have
        # more than one listener, hence the small dispatcher functions
        # instead of assigning a dock method directly).
        # _restore_roles() (inside FilePickerDock's own __init__, already
        # ran) may have already restored a role from a previous session
        # before these callbacks existed to hear about it — push the
        # current values once explicitly so a restored assignment isn't
        # silently missed.
        def _on_cells_file_changed(path):
            self.extract_dock.set_target_file(path)
            self.placer_dock.set_cells_file(path)
            self.cell_list_dock.set_cells_file(path)

        def _on_placer_file_changed(path):
            self.extract_dock.set_placer_file(path)
            self.placer_dock.set_placer_file(path)
            self.placer_list_dock.set_placer_file(path)

        self.file_picker_dock.on_cells_file_changed = _on_cells_file_changed
        self.file_picker_dock.on_extractor_file_changed = self.extract_dock.set_profile_file
        self.file_picker_dock.on_placer_file_changed = _on_placer_file_changed
        _on_cells_file_changed(self.file_picker_dock.assigned["cells"])
        self.extract_dock.set_profile_file(self.file_picker_dock.assigned["extractor"])
        _on_placer_file_changed(self.file_picker_dock.assigned["placer"])

        # Components tree -> Placer: clicking a Cluster group node in the
        # tree fills PlacerDock's Cluster field; Cells tab -> Placer:
        # clicking a Cell fills PlacerDock's Cell field (both requested
        # live 2026-08-01 as the natural place to pick from, instead of a
        # picker embedded in PlacerDock itself).
        self.tree_dock.on_cluster_picked = self.placer_dock.set_cluster_name
        self.cell_list_dock.on_cell_picked = self.placer_dock.set_selected_cell
        # Placements tab -> Placer: clicking an already-saved clone_placement
        # re-opens it in the form for editing/Redraw; Placer -> Placements
        # tab the other way: a successful Save refreshes the list so a
        # brand new (or renamed) entry shows up without reassigning Files.
        self.placer_list_dock.on_placement_picked = self.placer_dock.load_placement
        self.placer_dock.on_saved = self.placer_list_dock.refresh

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
        if data.get("tray_enabled"):
            self.tray_checkbox.setChecked(True)  # triggers _set_tray_enabled via its signal

    def _persist_settings(self) -> None:
        rect = self.geometry()
        data = settings.load()
        data["window_geometry"] = {"x": rect.x(), "y": rect.y(),
                                    "width": rect.width(), "height": rect.height()}
        data["always_on_top"] = self.always_on_top_checkbox.isChecked()
        data["tray_enabled"] = self.tray_checkbox.isChecked()
        settings.save(data)

    def closeEvent(self, event) -> None:
        """While the tray icon is enabled, the title-bar X hides instead of
        quitting — reachable again via the tray (see _set_tray_enabled/
        _toggle_visibility). Real quit only happens here when tray is off
        (today's original behavior, unchanged) or via the tray menu's Quit
        action, which bypasses this entirely (see _quit)."""
        if self.tray_checkbox.isChecked():
            event.ignore()
            self.hide()
            return
        self._persist_settings()
        super().closeEvent(event)

    def _set_always_on_top(self, checked: bool) -> None:
        """setWindowFlag() only takes effect on the next show() — the window
        briefly disappears and reappears on most platforms (X11/Windows),
        which is the normal/expected way Qt does this, not a bug here."""
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()

    # ── Tray icon ────────────────────────────────────────────────────────

    def _set_tray_enabled(self, checked: bool) -> None:
        if checked:
            if self._tray_icon is not None:
                return
            if not QSystemTrayIcon.isSystemTrayAvailable():
                logger.warning(_("Tray icon requested but no system tray is available here."))
            self._tray_icon = QSystemTrayIcon(build_tray_icon(), self)
            self._tray_icon.setToolTip(_("KiCadStamp"))
            menu = QMenu()
            menu.addAction(_("Show/Hide"), self._toggle_visibility)
            menu.addAction(_("Open fieldstool"), self.open_fieldstool)
            menu.addSeparator()
            menu.addAction(_("Quit"), self._quit)
            self._tray_icon.setContextMenu(menu)
            self._tray_icon.activated.connect(self._on_tray_activated)
            self._tray_icon.show()
        else:
            if self._tray_icon is not None:
                self._tray_icon.hide()
                self._tray_icon = None

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._toggle_visibility()

    def _toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.bring_to_front()

    def bring_to_front(self) -> None:
        """Un-hides/raises this window — called from the tray's Show/Hide
        action, and from SingleInstanceGuard.activation_requested when a
        second launch attempt pings this already-running instance
        (see kicadstamp_gui.py)."""
        self.show()
        self.raise_()
        self.activateWindow()

    def open_fieldstool(self) -> None:
        """Un-hides the main window if tray-hidden, and brings the
        fieldstool tab to front even if another right-hand tab is active or
        the dock was individually closed — used by both the tray menu and
        the status-bar button."""
        self.bring_to_front()
        self.fieldstool_dock.setVisible(True)
        self.fieldstool_dock.raise_()

    def _quit(self) -> None:
        """Tray menu's Quit — a real quit regardless of the tray checkbox.
        QApplication.quit() doesn't invoke closeEvent on any window (it just
        stops the event loop), so this deliberately bypasses self.close()/
        closeEvent entirely rather than needing a "really quit" flag."""
        self._persist_settings()
        QApplication.instance().quit()

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
            self.placer_dock.refresh_known_roles(self.connection.board)
            self.placer_dock.refresh_known_nets(self.connection.board)

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
        self.extract_dock.set_board_selection(items, selected)
