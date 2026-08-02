# tests/gui/test_pending_dock.py
from gui.docks.pending import PendingChangesDock, PendingRegistry


# ── PendingRegistry — no Qt dependency, testable without a QApplication ─────

def test_stage_and_entries(tmp_path):
    reg = PendingRegistry(tmp_path / "pending.json")
    reg.stage("R1", "Role", "NEW_A")
    reg.stage("R2", "Cluster", "Cl_B")

    entries = reg.entries()
    assert len(entries) == 2
    assert {(e.ref, e.field, e.new_value) for e in entries} == {
        ("R1", "Role", "NEW_A"), ("R2", "Cluster", "Cl_B")}


def test_restaging_same_ref_field_overwrites(tmp_path):
    reg = PendingRegistry(tmp_path / "pending.json")
    reg.stage("R1", "Role", "FIRST")
    reg.stage("R1", "Role", "SECOND")

    entries = reg.entries()
    assert len(entries) == 1 and entries[0].new_value == "SECOND"


def test_remove(tmp_path):
    reg = PendingRegistry(tmp_path / "pending.json")
    reg.stage("R1", "Role", "A")
    reg.stage("R2", "Role", "B")
    reg.remove("R1", "Role")

    assert [e.ref for e in reg.entries()] == ["R2"]


def test_clear(tmp_path):
    reg = PendingRegistry(tmp_path / "pending.json")
    reg.stage("R1", "Role", "A")
    reg.clear()
    assert reg.entries() == []


def test_persists_across_instances(tmp_path):
    path = tmp_path / "pending.json"
    reg1 = PendingRegistry(path)
    reg1.stage("R1", "Role", "A")

    reg2 = PendingRegistry(path)
    assert [e.ref for e in reg2.entries()] == ["R1"]


def test_missing_file_starts_empty(tmp_path):
    reg = PendingRegistry(tmp_path / "does_not_exist.json")
    assert reg.entries() == []


def test_corrupt_file_starts_empty_not_fatal(tmp_path):
    path = tmp_path / "pending.json"
    path.write_text("{not valid json", encoding="utf-8")
    reg = PendingRegistry(path)
    assert reg.entries() == []


def test_as_fields_cfg_groups_by_ref():
    reg = PendingRegistry.__new__(PendingRegistry)
    reg._entries = {("R1", "Role"): "A", ("R1", "Cluster"): "B", ("R2", "Role"): "C"}

    cfg = reg.as_fields_cfg()
    assert cfg == {"R1": {"Role": "A", "Cluster": "B"}, "R2": {"Role": "C"}}


# ── PendingChangesDock — the Qt wrapper around PendingRegistry ──────────────

def _dock(main_window, tmp_path):
    registry = PendingRegistry(tmp_path / "pending.json")
    return PendingChangesDock(main_window, registry)


def test_stage_populates_table(qapp, main_window, tmp_path):
    dock = _dock(main_window, tmp_path)
    dock.stage("R1", "Role", "R_A")

    assert dock.table.rowCount() == 1
    assert dock.table.item(0, 0).text() == "R1"
    assert dock.table.item(0, 1).text() == "Role"
    assert dock.table.item(0, 2).text() == "R_A"
    assert dock.apply_button.isEnabled()


def test_stage_group_stages_one_row_per_ref(qapp, main_window, tmp_path):
    dock = _dock(main_window, tmp_path)
    dock.stage_group(["R1", "R2", "R3"], "Cluster", "Cl_A")

    assert dock.table.rowCount() == 3
    assert {dock.table.item(r, 0).text() for r in range(3)} == {"R1", "R2", "R3"}


def test_apply_button_disabled_when_empty(qapp, main_window, tmp_path):
    dock = _dock(main_window, tmp_path)
    assert not dock.apply_button.isEnabled()


def test_clear_empties_table_and_disables_apply(qapp, main_window, tmp_path):
    dock = _dock(main_window, tmp_path)
    dock.stage("R1", "Role", "R_A")
    dock._on_clear()

    assert dock.table.rowCount() == 0
    assert not dock.apply_button.isEnabled()


def test_remove_selected_removes_only_that_row(qapp, main_window, tmp_path):
    dock = _dock(main_window, tmp_path)
    dock.stage("R1", "Role", "A")
    dock.stage("R2", "Role", "B")
    dock.table.selectRow(0)
    dock._on_remove_selected()

    assert dock.table.rowCount() == 1
    assert dock.table.item(0, 0).text() == "R2"


def test_apply_button_click_calls_callback(qapp, main_window, tmp_path):
    dock = _dock(main_window, tmp_path)
    dock.stage("R1", "Role", "A")
    calls = []
    dock.on_apply_clicked = lambda: calls.append(True)
    dock.apply_button.click()

    assert calls == [True]
