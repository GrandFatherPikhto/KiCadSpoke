# tests/fieldstool_gui/test_pending_dock.py
from fieldstool.gui.pending import PendingChangesDock, PendingRegistry


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
