# tests/gui/test_fieldstool_window.py
"""
gui.fieldstool_window.MainWindow tests are headless AND .kicad_sch-
mutation-free except for the one deliberate "apply succeeds" test, which
DOES write a throwaway tmp_path fixture (never anything under the real
repo) to prove the whole staging -> Apply -> write chain actually
round-trips, not just that each piece is individually plausible.
"""
from pathlib import Path

from gui import fieldstool_window as fieldstool_window_mod
from tests.fieldstool_fixtures import sch_file, symbol_block
from tests.gui.conftest import _FakeConnection


def _write_root(tmp_path, *blocks):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(*blocks), encoding="utf-8")
    return root


def test_set_root_sheet_populates_components_and_combos(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A", cluster="Cl_A"))
    fieldstool_window._set_root_sheet(root)

    assert len(fieldstool_window._components) == 1
    assert fieldstool_window.role_combo.findText("R_A") != -1
    assert fieldstool_window.cluster_combo.findText("Cl_A") != -1


def test_rescan_fires_on_components_changed_callback(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A"))
    calls = []
    fieldstool_window.on_components_changed = lambda: calls.append(1)

    fieldstool_window._set_root_sheet(root)  # _set_root_sheet triggers _rescan() internally

    assert calls == [1]


def test_rescan_with_no_callback_set_does_not_raise(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A"))
    assert fieldstool_window.on_components_changed is None

    fieldstool_window._set_root_sheet(root)  # must not raise AttributeError


def test_group_picked_sets_targets_and_prefills_combo(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1", "R2"], role="R_A"))
    fieldstool_window._set_root_sheet(root)

    fieldstool_window._on_group_picked("Role", "R_A", ["R1", "R2"])

    assert sorted(fieldstool_window._current_targets) == ["R1", "R2"]
    assert fieldstool_window.role_combo.currentText() == "R_A"
    assert fieldstool_window.stage_button.isEnabled()


def test_group_picked_also_fills_the_other_field_when_it_happens_to_be_uniform(fieldstool_window, tmp_path):
    # Grouped by Role, but both members also happen to share the same
    # Cluster — _prefill_combos_for_refs should fill that too, not just the
    # field being grouped by.
    root = _write_root(
        tmp_path,
        symbol_block(["R1"], role="R_A", cluster="Cl_A"),
        symbol_block(["R2"], role="R_A", cluster="Cl_A"),
    )
    fieldstool_window._set_root_sheet(root)

    fieldstool_window._on_group_picked("Role", "R_A", ["R1", "R2"])

    assert fieldstool_window.role_combo.currentText() == "R_A"
    assert fieldstool_window.cluster_combo.currentText() == "Cl_A"


def test_leaf_picked_prefills_both_combos_from_existing_values(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A", cluster="Cl_A"))
    fieldstool_window._set_root_sheet(root)

    fieldstool_window._on_tree_leaf_picked(["R1"])

    assert fieldstool_window.role_combo.currentText() == "R_A"
    assert fieldstool_window.cluster_combo.currentText() == "Cl_A"


def test_leaf_picked_clears_combos_when_targets_differ(fieldstool_window, tmp_path):
    root = _write_root(
        tmp_path,
        symbol_block(["R1"], role="R_A", cluster="Cl_A"),
        symbol_block(["R2"], role="R_B", cluster="Cl_B"),
    )
    fieldstool_window._set_root_sheet(root)
    # Prime the combos with a stale value from an earlier pick — must be
    # cleared, not left showing a misleading single value, once the new
    # pick turns out to be mixed.
    fieldstool_window.role_combo.setCurrentText("STALE")
    fieldstool_window.cluster_combo.setCurrentText("STALE")

    fieldstool_window._on_tree_leaf_picked(["R1", "R2"])

    assert fieldstool_window.role_combo.currentText() == ""
    assert fieldstool_window.cluster_combo.currentText() == ""


def test_stage_writes_to_pending_registry(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)

    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()

    entries = fieldstool_window._pending_registry.entries()
    assert len(entries) == 1
    assert entries[0].ref == "R1" and entries[0].field == "Role" and entries[0].new_value == "NEW"


def test_stage_with_no_target_does_nothing(fieldstool_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)

    fieldstool_window._set_targets([])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()  # no targets -> loop body never runs

    assert fieldstool_window._pending_registry.entries() == []


def test_apply_blocked_when_kicad_running(fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()

    monkeypatch.setattr(fieldstool_window_mod, "check_kicad_not_running",
                        lambda force: (_ for _ in ()).throw(RuntimeError("kicad running")))
    write_calls = []
    monkeypatch.setattr(fieldstool_window_mod, "write_files",
                        lambda *a, **k: write_calls.append(1) or ([], []))
    shown = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    fieldstool_window._on_apply()

    assert write_calls == []  # never reached the write path
    assert shown == ["info"]
    assert len(fieldstool_window._pending_registry.entries()) == 1  # still staged, nothing consumed


def test_apply_with_nothing_pending_shows_message(fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)

    monkeypatch.setattr(fieldstool_window_mod, "check_kicad_not_running", lambda force: None)
    shown = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    fieldstool_window._on_apply()
    assert shown == ["info"]


def test_apply_succeeds_writes_file_and_clears_pending(fieldstool_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    fieldstool_window._set_root_sheet(root)
    fieldstool_window._set_targets(["R1"])
    fieldstool_window.role_combo.setCurrentText("NEW")
    fieldstool_window._on_stage()

    monkeypatch.setattr(fieldstool_window_mod, "check_kicad_not_running", lambda force: None)
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: fieldstool_window_mod.QMessageBox.StandardButton.Yes))
    shown = []
    monkeypatch.setattr(fieldstool_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    fieldstool_window._on_apply()

    assert '"Role" "NEW"' in root.read_text(encoding="utf-8")
    assert fieldstool_window._pending_registry.entries() == []
    assert shown == ["info"]
    assert Path(str(root) + ".bak").exists()


# ── shared-connection public hooks ──────────────────────────────────────────

def test_set_live_selection_sets_targets_and_enables_stage(fieldstool_window):
    fieldstool_window.set_live_selection(["R1", "R2"])
    assert fieldstool_window._current_targets == ["R1", "R2"]
    assert fieldstool_window.stage_button.isEnabled()


def test_set_live_selection_empty_is_noop(fieldstool_window):
    fieldstool_window._set_targets(["R1"])
    fieldstool_window.set_live_selection([])
    assert fieldstool_window._current_targets == ["R1"]


def test_set_connection_status_updates_label(fieldstool_window):
    fieldstool_window.set_connection_status(None)
    assert "Connected" in fieldstool_window.status_label.text()
    fieldstool_window.set_connection_status("boom")
    assert "boom" in fieldstool_window.status_label.text()


def test_connection_is_the_injected_one(qapp):
    """MainWindow never creates its own BoardConnection — the embedding main
    GUI always injects its own (one kipy client, one REQ socket, one polling
    loop feeding this window through set_connection_status()/
    set_live_selection())."""
    shared = _FakeConnection()
    window = fieldstool_window_mod.MainWindow(connection=shared)
    assert window.connection is shared


def test_push_selection_to_board_gated_during_long_op(fieldstool_window):
    """While a background long op (Extract/Redraw) holds the shared socket,
    a tree-pick must not fire select_items() into it (that would interleave
    a second request into the op's in-flight REQ)."""
    from types import SimpleNamespace
    select_calls = []

    class _Adapter:
        def get_footprint(self, ref):
            return SimpleNamespace(ref=ref)

        def select_items(self, footprints):
            select_calls.append([fp.ref for fp in footprints])

    fieldstool_window.connection.board = SimpleNamespace(adapter=_Adapter())
    # is_connected is a property (board is not None) -> the pick would
    # normally reach adapter.select_items(); only the long-op flag blocks it.

    fieldstool_window.connection.long_op_active = True
    fieldstool_window._push_selection_to_board(["R1"])
    assert select_calls == []

    fieldstool_window.connection.long_op_active = False
    fieldstool_window._push_selection_to_board(["R1", "R2"])
    assert select_calls == [["R1", "R2"]]
