# tests/gui/test_pending_dock.py
from gui.docks.pending import (PendingChangesDock, PendingEdit,
                                compute_pending_edits, edits_to_fields_cfg)
from gui.schema_model import SchematicComponent
from kicadstamp.explore import Selected


def _component(ref, role, cluster, divergent=False):
    return SchematicComponent(ref=ref, role=role, cluster=cluster, file="x.kicad_sch",
                              block_start=0, divergent=divergent)


def _selected(ref, role, cluster):
    return Selected(ref=ref, role=role, cluster=cluster, sheet=[], nets={}, fp=None)


# ── compute_pending_edits — no Qt dependency, testable without a QApplication ──

def test_no_edits_when_board_matches_schematic():
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = [_selected("R1", "ROLE_A", "CL_A")]

    assert compute_pending_edits(components, snapshot) == []


def test_role_diff_detected():
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = [_selected("R1", "ROLE_B", "CL_A")]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Role", "ROLE_A", "ROLE_B")]


def test_cluster_diff_detected():
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = [_selected("R1", "ROLE_A", "CL_B")]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Cluster", "CL_A", "CL_B")]


def test_both_fields_diff_detected():
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = [_selected("R1", "ROLE_B", "CL_B")]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Cluster", "CL_A", "CL_B"),
                      PendingEdit("R1", "Role", "ROLE_A", "ROLE_B")]


def test_erasing_on_the_board_is_a_diff_too():
    """Regression: Clear all blanks Role/Cluster on the live board — that
    must show up as a pending edit (new_value == ''), not be swallowed as
    "nothing changed" just because it's now falsy."""
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = [_selected("R1", None, None)]

    edits = compute_pending_edits(components, snapshot)

    assert edits == [PendingEdit("R1", "Cluster", "CL_A", ""),
                      PendingEdit("R1", "Role", "ROLE_A", "")]


def test_ref_only_on_board_is_ignored():
    """Not yet in the schematic tree this session (or a stale/removed part
    number) — nothing to diff against."""
    components = []
    snapshot = [_selected("R1", "ROLE_A", "CL_A")]

    assert compute_pending_edits(components, snapshot) == []


def test_ref_only_in_schematic_is_ignored():
    """Not currently on the board — nothing to diff against."""
    components = [_component("R1", "ROLE_A", "CL_A")]
    snapshot = []

    assert compute_pending_edits(components, snapshot) == []


def test_edits_sorted_by_ref_then_field():
    components = [_component("R2", "A", "A"), _component("R1", "A", "A")]
    snapshot = [_selected("R2", "B", "B"), _selected("R1", "B", "B")]

    edits = compute_pending_edits(components, snapshot)

    assert [(e.ref, e.field) for e in edits] == [
        ("R1", "Cluster"), ("R1", "Role"), ("R2", "Cluster"), ("R2", "Role")]


def test_edits_to_fields_cfg_groups_by_ref():
    edits = [PendingEdit("R1", "Role", "A", "NEW_A"), PendingEdit("R1", "Cluster", "B", "NEW_B"),
             PendingEdit("R2", "Role", "C", "NEW_C")]

    cfg = edits_to_fields_cfg(edits)

    assert cfg == {"R1": {"Role": "NEW_A", "Cluster": "NEW_B"}, "R2": {"Role": "NEW_C"}}


# ── PendingChangesDock — the Qt wrapper, fed by set_edits() ─────────────────

def test_set_edits_populates_table(qapp, main_window):
    dock = PendingChangesDock(main_window)
    dock.set_edits([PendingEdit("R1", "Role", "OLD", "NEW")])

    assert dock.table.rowCount() == 1
    assert dock.table.item(0, 0).text() == "R1"
    assert dock.table.item(0, 1).text() == "Role"
    assert dock.table.item(0, 2).text() == "OLD"
    assert dock.table.item(0, 3).text() == "NEW"
    assert dock.apply_button.isEnabled()


def test_apply_button_disabled_when_empty(qapp, main_window):
    dock = PendingChangesDock(main_window)
    assert not dock.apply_button.isEnabled()


def test_set_edits_empty_disables_apply_and_clears_table(qapp, main_window):
    dock = PendingChangesDock(main_window)
    dock.set_edits([PendingEdit("R1", "Role", "OLD", "NEW")])

    dock.set_edits([])

    assert dock.table.rowCount() == 0
    assert not dock.apply_button.isEnabled()


def test_apply_button_click_calls_callback(qapp, main_window):
    dock = PendingChangesDock(main_window)
    dock.set_edits([PendingEdit("R1", "Role", "OLD", "NEW")])
    calls = []
    dock.on_apply_clicked = lambda: calls.append(True)

    dock.apply_button.click()

    assert calls == [True]
