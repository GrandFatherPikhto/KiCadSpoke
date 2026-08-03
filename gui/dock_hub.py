# gui/dock_hub.py
"""
DockHub — owns every dock in the KiCadStamp main window: construction,
layout (add/tabify onto the owning QMainWindow) and all dock-to-dock signal
wiring. MainWindow keeps ownership of the window and BoardConnection only
(Phase 3.3 of the gui-optimization roadmap) and talks to its docks through
this controller, which is the single place dock coordination grows.

The docks are QDockWidgets parented to the QMainWindow, so Qt owns their
lifetime; DockHub creates/arranges/connects them and holds the references
MainWindow re-exposes as thin forwarding properties — needed for the parts
of the app that still reach a dock directly (notably RoleClusterTreeDock's
lazy fieldstool lookup and the test suite).
"""
from PyQt6.QtCore import Qt

from .docks.config_tree import ConfigTreeDock
from .docks.extract import ExtractDock
from .docks.fieldstool_dock import FieldsToolDock
from .docks.log_panel import LogDock
from .docks.placer import PlacerDock
from .docks.role_cluster_tree import RoleClusterTreeDock


class DockHub:
    """Constructs, lays out and wires every dock of the KiCadStamp main
    window. MainWindow creates one DockHub with its BoardConnection and then
    drives the docks through this controller's delegates."""

    def __init__(self, main_window, connection, verbose: bool = False):
        self.main_window = main_window

        # ── left group: Components tree, Config tree ──────────────────────
        self.tree_dock = RoleClusterTreeDock(main_window, connection=connection)
        main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)

        self.config_tree_dock = ConfigTreeDock(main_window)
        main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.config_tree_dock)
        main_window.tabifyDockWidget(self.tree_dock, self.config_tree_dock)

        # ── right group: fieldstool, Extract-to-file, Placer ──────────────
        self.fieldstool_dock = FieldsToolDock(main_window, connection=connection)
        main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.fieldstool_dock)

        self.extract_dock = ExtractDock(main_window, connection=connection)
        main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.extract_dock)
        main_window.tabifyDockWidget(self.fieldstool_dock, self.extract_dock)

        self.placer_dock = PlacerDock(main_window)
        main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.placer_dock)
        main_window.tabifyDockWidget(self.extract_dock, self.placer_dock)

        # ── bottom: log ───────────────────────────────────────────────────
        self.log_dock = LogDock(main_window, verbose=verbose)
        main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)

        self._wire()

    def restore_tree_mode(self) -> None:
        """Restores the Components tree's "Not yet applied" (schematic)
        mode. Deliberately NOT part of __init__: restoring it rebuilds the
        tree, and that rebuild reads main_window.fieldstool_dock through the
        tree dock's lazy lookup — which cannot resolve until MainWindow has
        bound its DockHub (see RoleClusterTreeDock.restore_mode_from_
        settings()). MainWindow calls this right after constructing the hub.
        """
        self.tree_dock.restore_mode_from_settings()

    def _wire(self) -> None:
        """Every dock-to-dock connection (real pyqtSignals — a role can
        legitimately have more than one listener)."""

        # Config tree -> Extract/Placer (2026-08-03, replaces FilePickerDock
        # entirely — see gui/docks/config_tree.py's module docstring):
        # file_selected fires on every click anywhere in the tree, and
        # feeds ALL of ExtractDock's/PlacerDock's file targets at once —
        # what used to be three independently-assigned roles (Cells/
        # Extractor/Placer) collapse into "whichever file is currently
        # being browsed".
        self.config_tree_dock.file_selected.connect(self.extract_dock.set_target_file)
        self.config_tree_dock.file_selected.connect(self.extract_dock.set_profile_file)
        self.config_tree_dock.file_selected.connect(self.extract_dock.set_placer_file)
        self.config_tree_dock.file_selected.connect(self.placer_dock.set_cells_file)
        self.config_tree_dock.file_selected.connect(self.placer_dock.set_placer_file)

        # Components tree -> Placer: clicking a Cluster group node in the
        # tree fills PlacerDock's Cluster field; Config tree -> Placer/
        # Extract: clicking a Cell/Clone placement/Extract profile leaf
        # routes into the matching existing form (2026-08-03, GUI tree
        # roadmap Этап 1 — replaces the old CellListDock/PlacerListDock
        # wiring, same target methods, unified single source).
        self.tree_dock.cluster_picked.connect(self.placer_dock.set_cluster_name)
        self.config_tree_dock.cell_picked.connect(self.placer_dock.set_selected_cell)
        self.config_tree_dock.placement_picked.connect(self.placer_dock.load_placement)
        self.config_tree_dock.profile_picked.connect(self.extract_dock.pick_profile)
        # Placer -> Config tree: a successful Save refreshes the whole tree
        # (walk_include_tree() is re-run) so a brand new (or renamed)
        # placement shows up without reassigning Files.
        self.placer_dock.saved.connect(self.config_tree_dock.refresh)
        # Config tree's "Add placer..." context-menu action -> Placer:
        # opens the form blank, targeting the file the action was invoked
        # on, and brings that tab to front (same raise pattern as
        # open_fieldstool() below).
        self.config_tree_dock.add_placer_requested.connect(self._start_new_placement)

        # fieldstool tab -> Components tree: an explicit Rescan/Apply there
        # refreshes this tree's schematic view (see FieldsToolDock).
        self.fieldstool_dock.components_changed.connect(self.tree_dock.refresh_schematic_view)

    # ── delegates MainWindow's poll/timer logic drives ────────────────────

    def push_snapshot(self, snapshot, board) -> None:
        """Feed a freshly rebuilt BoardConnection.snapshot into the docks
        that display it — the ONE consumer of the snapshot (see
        gui/main_window.py's _poll)."""
        self.tree_dock.set_footprints(snapshot)
        self.placer_dock.refresh_known_roles(snapshot)
        self.placer_dock.refresh_known_nets(board)

    def clear_components(self) -> None:
        """Connection-lost path: empty the Components tree (live mode only —
        set_footprints leaves an active schematic view untouched)."""
        self.tree_dock.set_footprints([])

    def highlight_selection(self, refs) -> None:
        """Board selection -> Components tree highlight (see
        gui/main_window.py's _poll_board_selection)."""
        self.tree_dock.highlight_board_selection(refs)

    def set_board_selection(self, items, selected) -> None:
        """Push the live selection into ExtractDock (its aliases/origin
        combos and button state depend on what's currently selected)."""
        self.extract_dock.set_board_selection(items, selected)

    def push_fieldstool_selection(self, refs) -> None:
        """Live board selection -> embedded fieldstool's target label (Phase
        5.1 — the main GUI's single 400ms tick now feeds BOTH the tree/
        ExtractDock and the embedded fieldstool, whose own selection timer is
        stopped when it shares the main connection)."""
        self.fieldstool_dock.push_live_selection(refs)

    def push_fieldstool_connection_status(self, error) -> None:
        """Mirror the shared connection's state into the embedded
        fieldstool's status label (Phase 5.1 — its own connect/refresh poll
        is stopped when it shares the main connection)."""
        self.fieldstool_dock.set_connection_status(error)

    def open_fieldstool(self) -> None:
        """Bring the fieldstool tab to front even if another right-hand tab
        is active or the dock was individually closed."""
        self.fieldstool_dock.setVisible(True)
        self.fieldstool_dock.raise_()

    def _start_new_placement(self, placer_path) -> None:
        """ConfigTreeDock's add_placer_requested delegate — resets
        PlacerDock's form and brings its tab to front, same reasoning as
        open_fieldstool() above (the action was invoked from the Config
        tree tab, not the Placer tab)."""
        self.placer_dock.new_placement(placer_path)
        self.placer_dock.setVisible(True)
        self.placer_dock.raise_()
