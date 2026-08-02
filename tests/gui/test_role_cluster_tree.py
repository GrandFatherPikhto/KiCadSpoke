# tests/gui/test_role_cluster_tree.py
from unittest.mock import Mock

from gui.schema_model import SchematicComponent

from gui import settings
from gui.docks.role_cluster_tree import RoleClusterTreeDock


class FakeSelected:
    def __init__(self, ref, role, cluster):
        self.ref, self.role, self.cluster = ref, role, cluster
        self.fp = object()


def test_group_by_persists_across_restart(main_window):
    dock = RoleClusterTreeDock(main_window)
    assert dock.group_by.currentIndex() == 0  # Role, the default

    dock.group_by.setCurrentIndex(1)  # Cluster
    assert settings.load()["tree_group_by"] == 1

    restarted = RoleClusterTreeDock(main_window)  # simulates a fresh launch
    assert restarted.group_by.currentIndex() == 1


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


def test_clicking_a_cluster_group_node_fires_cluster_picked_signal(main_window):
    dock = RoleClusterTreeDock(main_window)
    dock.group_by.setCurrentIndex(1)  # Cluster grouping
    dock.set_footprints([
        FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER"),
        FakeSelected("C2", "C_IN", "Channel_2/PI_FILTER"),
    ])

    picked = []
    dock.cluster_picked.connect(picked.append)

    model = dock.tree.model()
    top_level = _find_item(model, "Channel_1")
    nested = _find_item(model, "PI_FILTER")  # first match, under Channel_1

    dock._on_clicked(model.indexFromItem(top_level))
    assert picked == ["Channel_1"]

    dock._on_clicked(model.indexFromItem(nested))
    assert picked == ["Channel_1", "Channel_1/PI_FILTER"]


def test_collapse_all_survives_a_later_rebuild(main_window):
    # Regression test for the "snaps back open on the next poll tick" bug:
    # _rebuild() runs on every set_footprints() call (simulating a ~2s poll
    # tick), and its expanded-state restore used to treat "nothing expanded"
    # as "first build ever" every time, re-expanding depth 0 regardless of
    # whether the user had just collapsed everything on purpose.
    dock = RoleClusterTreeDock(main_window)
    dock.group_by.setCurrentIndex(1)  # Cluster grouping
    footprints = [
        FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER"),
        FakeSelected("C2", "C_IN", "Channel_2/PI_FILTER"),
    ]
    dock.set_footprints(footprints)

    top_level = _find_item(dock.tree.model(), "Channel_1")
    assert dock.tree.isExpanded(dock.tree.model().indexFromItem(top_level))

    dock.tree_collapse_all()
    assert not dock.tree.isExpanded(dock.tree.model().indexFromItem(top_level))

    dock.set_footprints(footprints)  # simulates the next poll tick
    top_level = _find_item(dock.tree.model(), "Channel_1")
    assert not dock.tree.isExpanded(dock.tree.model().indexFromItem(top_level))


def test_leaf_click_and_role_mode_do_not_fire_cluster_picked_signal(main_window):
    dock = RoleClusterTreeDock(main_window)
    dock.set_footprints([FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER")])

    picked = []
    dock.cluster_picked.connect(picked.append)

    # Role grouping (the default) — clicking a group here is a Role, not a Cluster.
    # _build_flat() suffixes group labels with a "(count)", unlike _build_hierarchical().
    model = dock.tree.model()
    role_group = _find_item(model, "C_IN (1)")
    dock._on_clicked(model.indexFromItem(role_group))
    assert picked == []

    # Cluster grouping, but a LEAF (component) click, not a group.
    dock.group_by.setCurrentIndex(1)
    model = dock.tree.model()
    leaf = _find_item(model, "C1")
    dock._on_clicked(model.indexFromItem(leaf))
    assert picked == []


# ── Schematic ("Not yet applied") mode — needs a real fieldstool_dock to
#    route into, so these use the real_main_window fixture (see
#    tests/gui/conftest.py), not the bare main_window stub. ─────────────────

def test_schematic_mode_not_restored_in_init_but_restore_method_works(real_main_window):
    dock = real_main_window.tree_dock
    assert not dock.mode_checkbox.isChecked()

    dock.mode_checkbox.setChecked(True)
    assert settings.load()["tree_schematic_mode"] is True

    restarted = RoleClusterTreeDock(real_main_window)
    assert not restarted.mode_checkbox.isChecked()  # NOT auto-restored in __init__
    restarted.restore_mode_from_settings()
    assert restarted.mode_checkbox.isChecked()


def test_schematic_leaf_click_routes_to_fieldstool_and_opens_tab(real_main_window):
    dock = real_main_window.tree_dock
    real_main_window.fieldstool_dock.window._components = [
        SchematicComponent("R1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    dock.mode_checkbox.setChecked(True)

    leaf_picked = Mock()
    open_fieldstool = Mock()
    real_main_window.fieldstool_dock.window._on_tree_leaf_picked = leaf_picked
    real_main_window.open_fieldstool = open_fieldstool

    leaf = _find_item(dock.tree.model(), "R1")
    dock._on_clicked(dock.tree.model().indexFromItem(leaf))

    leaf_picked.assert_called_once_with(["R1"])
    open_fieldstool.assert_called_once()


def test_schematic_group_click_uses_hierarchical_cluster_value(real_main_window):
    dock = real_main_window.tree_dock
    real_main_window.fieldstool_dock.window._components = [
        SchematicComponent("R1", "R_A", "Channel_1/PI_FILTER", "root.kicad_sch", 0, divergent=False),
    ]
    dock.mode_checkbox.setChecked(True)
    dock.group_by.setCurrentIndex(1)  # Cluster grouping -> hierarchical, matches live mode

    group_picked = Mock()
    real_main_window.fieldstool_dock.window._on_group_picked = group_picked
    real_main_window.open_fieldstool = Mock()

    nested = _find_item(dock.tree.model(), "PI_FILTER")
    dock._on_clicked(dock.tree.model().indexFromItem(nested))

    group_picked.assert_called_once_with("Cluster", "Channel_1/PI_FILTER", ["R1"])


def test_schematic_divergent_component_gets_warning_marker(real_main_window):
    dock = real_main_window.tree_dock
    real_main_window.fieldstool_dock.window._components = [
        SchematicComponent("R1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=True),
    ]
    dock.mode_checkbox.setChecked(True)

    assert _find_item(dock.tree.model(), "R1 ⚠") is not None


def test_set_footprints_does_not_clobber_active_schematic_view(real_main_window):
    dock = real_main_window.tree_dock
    real_main_window.fieldstool_dock.window._components = [
        SchematicComponent("SCH1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    dock.mode_checkbox.setChecked(True)
    assert _find_item(dock.tree.model(), "SCH1") is not None

    dock.set_footprints([FakeSelected("PCB1", "R_B", "Cl_B")])  # simulates a live poll tick

    assert _find_item(dock.tree.model(), "SCH1") is not None
    assert _find_item(dock.tree.model(), "PCB1") is None


def test_refresh_schematic_view_noop_in_live_mode_rebuilds_in_schematic_mode(real_main_window):
    dock = real_main_window.tree_dock
    real_main_window.fieldstool_dock.window._components = [
        SchematicComponent("R1", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]

    # Live mode (default) -> no-op: refresh_schematic_view() must not touch the
    # model at all (asserting the model OBJECT is unchanged, not its contents —
    # this dev machine may have a real KiCad reachable, which would otherwise
    # make an emptiness assumption flaky).
    model_before = dock.tree.model()
    dock.refresh_schematic_view()
    assert dock.tree.model() is model_before

    dock.mode_checkbox.setChecked(True)
    real_main_window.fieldstool_dock.window._components = [
        SchematicComponent("R2", "R_A", "Cl_A", "root.kicad_sch", 0, divergent=False),
    ]
    dock.refresh_schematic_view()  # schematic mode -> rebuilds with the fresh list
    assert _find_item(dock.tree.model(), "R2") is not None
