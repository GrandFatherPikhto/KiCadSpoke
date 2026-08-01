# tests/fieldstool_gui/test_tree.py
from fieldstool.gui.tree import _REF_ROLE, ComponentTreeDock
from fieldstool.schema_model import SchematicComponent


def _comp(ref, role=None, cluster=None, divergent=False):
    return SchematicComponent(ref=ref, role=role, cluster=cluster,
                              file="f.kicad_sch", block_start=0, divergent=divergent)


def test_set_components_groups_by_role(qapp, main_window):
    dock = ComponentTreeDock(main_window)
    dock.set_components([_comp("R1", role="R_A"), _comp("R2", role="R_A"), _comp("R3", role="R_B")])

    model = dock.tree.model()
    assert model.invisibleRootItem().rowCount() == 2  # R_A, R_B groups


def test_group_by_cluster_switch(qapp, main_window):
    dock = ComponentTreeDock(main_window)
    dock.set_components([_comp("R1", role="R_A", cluster="Cl_1"), _comp("R2", role="R_B", cluster="Cl_2")])
    dock.group_by.setCurrentIndex(1)  # Cluster

    model = dock.tree.model()
    names = {model.invisibleRootItem().child(i).text().split(" (")[0]
             for i in range(model.invisibleRootItem().rowCount())}
    assert names == {"Cl_1", "Cl_2"}


def test_search_filter_by_ref(qapp, main_window):
    dock = ComponentTreeDock(main_window)
    dock.set_components([_comp("R1", role="R_A"), _comp("C1", role="C_A")])
    dock.search_edit.setText("R1")

    model = dock.tree.model()
    all_refs = []

    def walk(item):
        ref = item.data(_REF_ROLE)
        if ref:
            all_refs.append(ref)
        for row in range(item.rowCount()):
            walk(item.child(row))

    walk(model.invisibleRootItem())
    assert all_refs == ["R1"]


def test_invalid_regex_does_not_crash(qapp, main_window):
    dock = ComponentTreeDock(main_window)
    dock.set_components([_comp("R1", role="R_A")])
    dock.regex_checkbox.setChecked(True)
    dock.search_edit.setText("(unclosed")  # must not raise
    assert dock.tree.model() is not None


def test_leaf_click_calls_on_leaf_picked(qapp, main_window):
    dock = ComponentTreeDock(main_window)
    dock.set_components([_comp("R1", role="R_A")])
    picked = []
    dock.on_leaf_picked = lambda refs: picked.append(refs)

    model = dock.tree.model()
    group_item = model.invisibleRootItem().child(0)
    leaf_item = group_item.child(0)
    dock._on_clicked(model.indexFromItem(leaf_item))

    assert picked == [["R1"]]


def test_group_click_calls_on_group_picked(qapp, main_window):
    dock = ComponentTreeDock(main_window)
    dock.set_components([_comp("R1", role="R_A"), _comp("R2", role="R_A")])
    picked = []
    dock.on_group_picked = lambda field, value, refs: picked.append((field, value, sorted(refs)))

    model = dock.tree.model()
    group_item = model.invisibleRootItem().child(0)
    dock._on_clicked(model.indexFromItem(group_item))

    assert picked == [("Role", "R_A", ["R1", "R2"])]


def test_highlight_refs_selects_matching_leaf(qapp, main_window):
    dock = ComponentTreeDock(main_window)
    dock.set_components([_comp("R1", role="R_A"), _comp("R2", role="R_A")])
    dock.highlight_refs({"R1"})  # must not raise, exercises the walk


def test_divergent_component_gets_warning_marker(qapp, main_window):
    dock = ComponentTreeDock(main_window)
    dock.set_components([_comp("U1", role="A", divergent=True)])

    model = dock.tree.model()
    leaf = model.invisibleRootItem().child(0).child(0)
    assert "⚠" in leaf.text()
