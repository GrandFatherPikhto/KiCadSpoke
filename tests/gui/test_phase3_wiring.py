# tests/gui/test_phase3_wiring.py
"""
Phase 3 (gui optimization roadmap, handoff_gui_optimization_2026_08_01.md):
real pyqtSignals replacing callable-attribute wiring (3.1) + BoardConnection
injection so docks stop reaching into main_window.connection deep (3.2).
These tests pin down the composition-root wiring in gui/main_window.py — the
Config tree's file_selected signal reaching every listener (2026-08-03,
replaced FilePickerDock's three independent role signals entirely — see
gui/docks/config_tree.py's module docstring), and the two connection-taking
docks using the injected object instead of main_window.connection.
"""
from types import SimpleNamespace
from unittest.mock import Mock

from gui.schema_model import SchematicComponent

from gui import settings
from gui.dock_hub import DockHub
from gui.docks.extract import ExtractDock
from gui.docks.role_cluster_tree import RoleClusterTreeDock
from gui.main_window import MainWindow


def _find_item(model, text):
    def walk(item):
        for row in range(item.rowCount()):
            child = item.child(row)
            if child.text() == text:
                return child
            found = walk(child)
            if found is not None:
                return found
        return None
    return walk(model.invisibleRootItem())


class _FakeSelected:
    def __init__(self, ref, role, cluster):
        self.ref, self.role, self.cluster = ref, role, cluster
        self.fp = object()


# ── 3.1: role signals propagate from the Files dock to every listener ───────

def _write(path):
    path.write_text("{}\n", encoding="utf-8")


def test_config_tree_file_selected_reaches_extract_and_placer(real_main_window, tmp_path):
    """file_selected (fired by ANY click in the Config tree, see
    gui/docks/config_tree.py's module docstring) feeds every one of
    ExtractDock's/PlacerDock's file targets at once — this REPLACES the
    three independent FilePickerDock role signals (Cells/Extractor/
    Placer), which no longer exist."""
    target_file = tmp_path / "power.yaml"
    _write(target_file)

    real_main_window.config_tree_dock.file_selected.emit(target_file)

    assert real_main_window.extract_dock._target_path == target_file
    assert real_main_window.extract_dock._profile_path == target_file
    assert real_main_window.extract_dock._placer_path == target_file
    assert real_main_window.placer_dock._cells_path == target_file
    assert real_main_window.placer_dock._placer_path == target_file


def test_config_tree_restores_last_root_after_restart(qapp, tmp_path):
    """A previous session's root file must be restored on startup —
    ConfigTreeDock._restore_last_root(), the replacement for
    FilePickerDock's restore_roles()."""
    root_file = tmp_path / "root.yaml"
    _write(root_file)

    data = settings.load()
    data["last_root_file"] = str(root_file)
    settings.save(data)

    window = MainWindow(timeout_ms=10, verbose=False)
    try:
        assert window.config_tree_dock._root_path == root_file
    finally:
        window._timer.stop()
        window._selection_timer.stop()


def test_tree_cluster_picked_fills_placer_cluster_field(real_main_window):
    """The Components-tree -> Placer wiring now goes through the
    cluster_picked signal — clicking a Cluster group node in the real window
    fills PlacerDock's Cluster field.

    real_main_window's tree_dock is wired to a LIVE BoardConnection on this
    machine, and clicking would highlight the real board through the real
    kipy adapter (our _FakeSelected.fp is not a kipy footprint). The signal
    is emitted before the board-highlight early-return, so swapping in a
    board-less connection still exercises the signal -> Placer wiring while
    keeping the live adapter untouched."""
    real_main_window.tree_dock.group_by.setCurrentIndex(1)  # Cluster grouping
    real_main_window.tree_dock._connection = SimpleNamespace(board=None)
    real_main_window.tree_dock.set_footprints([
        _FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER"),
    ])

    top_level = _find_item(real_main_window.tree_dock.tree.model(), "Channel_1")
    real_main_window.tree_dock._on_clicked(
        real_main_window.tree_dock.tree.model().indexFromItem(top_level))

    assert real_main_window.placer_dock.cluster_edit.text() == "Channel_1"


def test_cell_picked_fills_placer_selected_cell(real_main_window):
    """ConfigTreeDock -> PlacerDock wiring (cell_picked -> set_selected_cell,
    see gui/docks/config_tree.py's cell_picked docstring) — clicking a Cell
    leaf in the real Config tree must reach PlacerDock's Cell field
    end-to-end, not just via a direct set_selected_cell() call (already
    covered elsewhere, but never through the actual signal). Also brings
    the merged Detail dock's Placer page to front (2026-08-03 —
    gui/docks/detail_panel.py)."""
    real_main_window.config_tree_dock.cell_picked.emit("ldo_adj")

    assert real_main_window.placer_dock._selected_cell == "ldo_adj"
    assert "ldo_adj" in real_main_window.placer_dock.cell_label.text()
    assert real_main_window._dock_hub.detail_dock.stack.currentWidget() is real_main_window.placer_dock


def test_placement_picked_loads_into_placer_form(real_main_window):
    """ConfigTreeDock -> PlacerDock wiring (placement_picked -> load_placement,
    see gui/docks/config_tree.py's placement_picked docstring) — clicking an
    already-saved placement leaf in the real Config tree must reach
    PlacerDock's form end-to-end, not just via a direct load_placement()
    call (already covered elsewhere, but never through the actual signal)."""
    entry = {"name": "spoke_1", "cell": "ldo_adj", "xy": [1.5, 2.5]}
    real_main_window.config_tree_dock.placement_picked.emit(entry)

    assert real_main_window.placer_dock.cluster_edit.text() == "spoke_1"
    assert real_main_window.placer_dock._selected_cell == "ldo_adj"
    assert real_main_window.placer_dock.x_edit.text() == "1.5"
    assert real_main_window.placer_dock.y_edit.text() == "2.5"


def test_profile_picked_fills_extract_form(real_main_window, tmp_path):
    """ConfigTreeDock -> ExtractDock wiring (profile_picked -> pick_profile,
    see gui/docks/config_tree.py's profile_picked docstring / ExtractDock.
    pick_profile's docstring) — clicking an Extract-profile leaf in the real
    Config tree must reach ExtractDock's form end-to-end, not just via a
    direct pick_profile() call."""
    extractor_file = tmp_path / "profiles.yaml"
    extractor_file.write_text(
        "extract_profiles:\n  alpha_profile:\n    params: {ROLE: '+3V3'}\n", encoding="utf-8")
    real_main_window.extract_dock.set_profile_file(extractor_file)

    real_main_window.config_tree_dock.profile_picked.emit("alpha_profile")

    assert real_main_window.extract_dock.profile_key_edit.text() == "alpha_profile"
    assert real_main_window._dock_hub.detail_dock.stack.currentWidget() is real_main_window.extract_dock


def test_file_selected_alone_shows_root_page(real_main_window, tmp_path):
    """A plain file/category click (file_selected fires with no matching
    leaf signal) falls back to the Root page — Denis's chosen auto-switch
    rule for clicks the tree can't route more specifically (2026-08-03)."""
    real_main_window._dock_hub.detail_dock.show_placer()
    target_file = tmp_path / "power.yaml"
    target_file.write_text("{}\n", encoding="utf-8")

    real_main_window.config_tree_dock.file_selected.emit(target_file)

    assert real_main_window._dock_hub.detail_dock.stack.currentWidget() is real_main_window.root_metadata_dock


def test_cell_picked_overrides_the_file_selected_fallback(real_main_window):
    """A Cell-leaf click fires file_selected (-> Root fallback) THEN
    cell_picked (-> Placer) in that order (see config_tree.py's
    _on_clicked) — the more specific signal must win, ending on Placer,
    not Root."""
    real_main_window.config_tree_dock.file_selected.emit(None)
    real_main_window.config_tree_dock.cell_picked.emit("ldo_adj")

    assert real_main_window._dock_hub.detail_dock.stack.currentWidget() is real_main_window.placer_dock


def test_placer_saved_refreshes_config_tree_placements(real_main_window, tmp_path):
    """PlacerDock -> ConfigTreeDock wiring (saved -> refresh, see
    gui/docks/config_tree.py's refresh docstring) — a successful Save must
    reach ConfigTreeDock's Clone placements category end-to-end, not just
    via a direct call (the tree would otherwise go stale after Save
    without a file reassign).

    Asserts on real widget state (the tree picking up a change made on disk
    after the fact) rather than monkeypatching refresh() — a PyQt signal
    connection captures the bound method at connect() time, so patching the
    instance attribute afterwards would not be intercepted (same caveat as
    test_fieldstool_components_changed_refreshes_tree above)."""
    placer_file = tmp_path / "placer.yaml"
    _write(placer_file)
    real_main_window.config_tree_dock.set_root_file(placer_file)
    root_item = real_main_window.config_tree_dock.tree.topLevelItem(0)
    assert root_item.childCount() == 0

    placer_file.write_text(
        "clone_placements:\n  - name: spoke_1\n    cell: ldo_adj\n    xy: [0, 0]\n",
        encoding="utf-8")
    real_main_window.placer_dock.saved.emit()

    root_item = real_main_window.config_tree_dock.tree.topLevelItem(0)
    placements = root_item.child(0)
    assert placements.text(0) == "Clone placements"
    assert placements.child(0).text(0) == "spoke_1"


# ── 3.2: docks use the injected BoardConnection, not main_window.connection ──

def test_extract_dock_uses_injected_connection_when_given(main_window):
    injected = SimpleNamespace()
    dock = ExtractDock(main_window, connection=injected)
    assert dock._connection is injected


def test_extract_dock_falls_back_to_main_window_connection(main_window):
    dock = ExtractDock(main_window)
    assert dock._connection is main_window.connection


def test_extract_dock_feeds_injected_adapter_into_extraction(main_window, tmp_path, monkeypatch):
    """Behavioral proof the injected connection is what extraction runs on —
    the adapter reaching extract_template_from_selection comes from the
    injected board, not from main_window.connection (which stays board-less)."""
    import gui.docks.extract as extract_mod

    cells_file = tmp_path / "cells.yaml"
    _write(cells_file)

    captured = {}

    def _fake_extract(adapter, name, params=None, items=None,
                      net_template_role=None, annotations=None, **kwargs):
        captured["adapter"] = adapter
        captured["items"] = items
        return {"ref": "C1"}

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)

    adapter = object()
    injected = SimpleNamespace(board=SimpleNamespace(adapter=adapter))
    dock = ExtractDock(main_window, connection=injected)
    dock.set_target_file(cells_file)
    dock.name_edit.setText("c1")
    dock._raw_items = ["item1"]

    # Full success-path extract: synchronous core (the async _on_extract()
    # path would not have written captured[] by the asserts below).
    dock._do_extract()

    assert captured["adapter"] is adapter
    assert captured["items"] == ["item1"]


def test_tree_dock_uses_injected_connection_for_board_highlight(main_window):
    """Clicking a leaf in live mode highlights the selection through the
    INJECTED connection's board.adapter — main_window.connection here has no
    board at all, so any fallback to it would silently no-op."""
    selected = []

    class _FakeAdapter:
        def select_items(self, footprints):
            selected.append(footprints)

    injected = SimpleNamespace(board=SimpleNamespace(adapter=_FakeAdapter()))
    dock = RoleClusterTreeDock(main_window, connection=injected)
    fp = object()
    dock.set_footprints([_FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER")])
    dock._selected[0].fp = fp

    model = dock.tree.model()
    leaf = _find_item(model, "C1")
    dock._on_clicked(model.indexFromItem(leaf))

    assert selected == [[fp]]


# ── 3.1/3.2: fieldstool access goes through FieldsToolDock's delegates ──────

def test_fieldstool_components_changed_refreshes_tree(real_main_window):
    """The Fieldstool dock's components_changed signal is wired to
    tree_dock.refresh_schematic_view in MainWindow — proven behaviorally
    (attribute replacement can't intercept a PyQt signal, which captures the
    bound method at connect() time): swap the fieldstool window's component
    list, emit the signal, and assert the tree rebuilt with the new data."""
    dock = real_main_window.tree_dock
    real_main_window.fieldstool_dock.window._components = [
        SchematicComponent("R1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    dock.mode_checkbox.setChecked(True)  # schematic mode -> _rebuild shows R1
    assert _find_item(dock.tree.model(), "R1") is not None

    real_main_window.fieldstool_dock.window._components = [
        SchematicComponent("R2", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    real_main_window.fieldstool_dock.components_changed.emit()

    assert _find_item(dock.tree.model(), "R2") is not None
    assert _find_item(dock.tree.model(), "R1") is None


def test_fieldstool_pick_delegates_reach_the_window(real_main_window):
    leaf_picked = Mock()
    group_picked = Mock()
    real_main_window.fieldstool_dock.window._on_tree_leaf_picked = leaf_picked
    real_main_window.fieldstool_dock.window._on_group_picked = group_picked

    real_main_window.fieldstool_dock.pick_leaf(["R1"])
    leaf_picked.assert_called_once_with(["R1"])

    real_main_window.fieldstool_dock.pick_group("Cluster", "Channel_1/PI_FILTER", ["R1"])
    group_picked.assert_called_once_with("Cluster", "Channel_1/PI_FILTER", ["R1"])


# ── 3.3: DockHub controller owns docks + layout + wiring ──────────────────

def _teardown_hub(hub):
    """A DockHub constructed on the bare `main_window` fixture embeds a real
    fieldstool MainWindow and a LogDock (root-logger handler) that would
    otherwise leak across tests."""
    hub.log_dock.remove_handler()


def test_main_window_exposes_all_docks_through_the_hub(real_main_window):
    """MainWindow owns a DockHub and re-exposes each dock as a thin
    forwarding property — the public surface (tests, RoleClusterTreeDock's
    lazy fieldstool lookup) keeps working while the hub owns the docks."""
    hub = real_main_window._dock_hub
    assert isinstance(hub, DockHub)

    assert real_main_window.tree_dock is hub.tree_dock
    assert real_main_window.config_tree_dock is hub.config_tree_dock
    assert real_main_window.fieldstool_dock is hub.fieldstool_dock
    assert real_main_window.extract_dock is hub.extract_dock
    assert real_main_window.placer_dock is hub.placer_dock
    assert real_main_window.log_dock is hub.log_dock


def test_dock_hub_constructs_all_docks_and_wires_file_selected(main_window, tmp_path):
    """A standalone DockHub builds every dock on any QMainWindow and the
    Config tree's file_selected signal reaches every listener — the
    composition root works without a real MainWindow too."""
    target_file = tmp_path / "power.yaml"
    _write(target_file)

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert hub.tree_dock is not None
        assert hub.config_tree_dock is not None
        assert hub.fieldstool_dock is not None
        assert hub.extract_dock is not None
        assert hub.placer_dock is not None
        assert hub.root_metadata_dock is not None
        assert hub.log_dock is not None

        hub.config_tree_dock.file_selected.emit(target_file)
        assert hub.extract_dock._target_path == target_file
        assert hub.placer_dock._cells_path == target_file
        assert hub.root_metadata_dock._path == target_file
    finally:
        _teardown_hub(hub)


def test_dock_hub_injects_connection_into_connection_docks(main_window):
    """The connection passed to DockHub reaches the three docks that consume
    it (RoleClusterTreeDock, ExtractDock, and — Phase 5.1 — the embedded
    fieldstool window) — never main_window.connection."""
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert hub.tree_dock._connection is main_window.connection
        assert hub.extract_dock._connection is main_window.connection
        assert hub.fieldstool_dock.window.connection is main_window.connection
    finally:
        _teardown_hub(hub)


def test_dock_hub_omits_board_written_hook_on_a_plain_main_window(main_window):
    """DockHub works on any QMainWindow, not just the real one (see
    test_dock_hub_constructs_all_docks_and_wires_file_selected above) — a
    fake main_window has no request_refresh, so the hook must fall back to
    None rather than raising AttributeError."""
    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    try:
        assert hub.tree_dock.on_board_written is None
        assert hub.fieldstool_dock.window.on_board_written is None
    finally:
        _teardown_hub(hub)


def test_dock_hub_wires_board_written_hook_to_request_refresh(real_main_window):
    """2026-08-03 fix: Stage/Clear all write straight to the live board, but
    the automatic poll tick never refreshes on its own once already
    connected — without this wiring, Pending changes never picks up the
    write until the user notices and clicks Refresh themselves."""
    hub = real_main_window._dock_hub
    assert hub.tree_dock.on_board_written == real_main_window.request_refresh
    assert hub.fieldstool_dock.window.on_board_written == real_main_window.request_refresh


def test_dock_hub_restore_tree_mode_after_construction(real_main_window):
    """restore_tree_mode() is deliberately NOT part of __init__ (the tree
    dock's lazy fieldstool lookup needs main_window.fieldstool_dock
    resolvable, which only happens once MainWindow bound its hub) —
    MainWindow calls it right after construction."""
    hub = real_main_window._dock_hub
    assert not hub.tree_dock.mode_checkbox.isChecked()  # empty isolated settings

    data = settings.load()
    data["tree_schematic_mode"] = True
    settings.save(data)

    hub.restore_tree_mode()
    assert hub.tree_dock.mode_checkbox.isChecked()
    assert settings.load()["tree_schematic_mode"] is True


def test_dock_hub_delegates_route_to_the_right_docks(real_main_window, monkeypatch):
    """The poll/timer delegates MainWindow drives go through the hub and hit
    exactly the right dock — this is where that coordination grows."""
    hub = real_main_window._dock_hub

    pushed = {}
    monkeypatch.setattr(hub.tree_dock, "set_footprints",
                        lambda s: pushed.setdefault("tree", []).append(s))
    monkeypatch.setattr(hub.placer_dock, "refresh_known_roles",
                        lambda s: pushed.setdefault("roles", []).append(s))
    monkeypatch.setattr(hub.placer_dock, "refresh_known_nets",
                        lambda b: pushed.setdefault("nets", []).append(b))
    monkeypatch.setattr(hub.thermal_via_dock, "refresh_known_roles",
                        lambda s: pushed.setdefault("thermal_roles", []).append(s))
    monkeypatch.setattr(hub.thermal_via_dock, "refresh_known_nets",
                        lambda b: pushed.setdefault("thermal_nets", []).append(b))

    board, snapshot = object(), object()
    hub.push_snapshot(snapshot, board)
    assert pushed["tree"] == [snapshot]
    assert pushed["roles"] == [snapshot]
    assert pushed["nets"] == [board]
    assert pushed["thermal_roles"] == [snapshot]
    assert pushed["thermal_nets"] == [board]

    cleared = []
    monkeypatch.setattr(hub.tree_dock, "set_footprints", lambda s: cleared.append(s))
    hub.clear_components()
    assert cleared == [[]]

    highlighted = []
    monkeypatch.setattr(hub.tree_dock, "highlight_board_selection",
                        lambda refs: highlighted.append(refs))
    hub.highlight_selection({"R1"})
    assert highlighted == [{"R1"}]

    board_selected = []
    monkeypatch.setattr(hub.extract_dock, "set_board_selection",
                        lambda items, sel: board_selected.append((items, sel)))
    hub.set_board_selection(["raw"], ["sel"])
    assert board_selected == [(["raw"], ["sel"])]

    shown, raised = [], []
    monkeypatch.setattr(hub.fieldstool_dock, "setVisible", lambda v: shown.append(v))
    monkeypatch.setattr(hub.fieldstool_dock, "raise_", lambda: raised.append(True))
    hub.open_fieldstool()
    assert shown == [True]
    assert raised == [True]
