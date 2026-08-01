# tests/gui/test_role_cluster_tree.py
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


def test_clicking_a_cluster_group_node_fires_on_cluster_picked(main_window):
    dock = RoleClusterTreeDock(main_window)
    dock.group_by.setCurrentIndex(1)  # Cluster grouping
    dock.set_footprints([
        FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER"),
        FakeSelected("C2", "C_IN", "Channel_2/PI_FILTER"),
    ])

    picked = []
    dock.on_cluster_picked = picked.append

    model = dock.tree.model()
    top_level = _find_item(model, "Channel_1")
    nested = _find_item(model, "PI_FILTER")  # first match, under Channel_1

    dock._on_clicked(model.indexFromItem(top_level))
    assert picked == ["Channel_1"]

    dock._on_clicked(model.indexFromItem(nested))
    assert picked == ["Channel_1", "Channel_1/PI_FILTER"]


def test_leaf_click_and_role_mode_do_not_fire_on_cluster_picked(main_window):
    dock = RoleClusterTreeDock(main_window)
    dock.set_footprints([FakeSelected("C1", "C_IN", "Channel_1/PI_FILTER")])

    picked = []
    dock.on_cluster_picked = picked.append

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
