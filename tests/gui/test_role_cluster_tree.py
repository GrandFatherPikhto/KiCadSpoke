# tests/gui/test_role_cluster_tree.py
from gui import settings
from gui.docks.role_cluster_tree import RoleClusterTreeDock


def test_group_by_persists_across_restart(main_window):
    dock = RoleClusterTreeDock(main_window)
    assert dock.group_by.currentIndex() == 0  # Role, the default

    dock.group_by.setCurrentIndex(1)  # Cluster
    assert settings.load()["tree_group_by"] == 1

    restarted = RoleClusterTreeDock(main_window)  # simulates a fresh launch
    assert restarted.group_by.currentIndex() == 1
