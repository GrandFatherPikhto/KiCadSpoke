# tests/fieldstool_gui/test_main_window.py
"""
MainWindow tests are headless AND .kicad_sch-mutation-free except for the
one deliberate "apply succeeds" test, which DOES write a throwaway
tmp_path fixture (never anything under the real repo) to prove the whole
staging -> Apply -> write chain actually round-trips, not just that each
piece is individually plausible.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import fieldstool.gui.main_window as main_window_mod
from tests.fieldstool_fixtures import sch_file, symbol_block


def _write_root(tmp_path, *blocks):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(*blocks), encoding="utf-8")
    return root


def test_set_root_sheet_populates_tree_and_combos(main_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="R_A", cluster="Cl_A"))
    main_window._set_root_sheet(root)

    assert len(main_window.tree_dock._components) == 1
    assert main_window.role_combo.findText("R_A") != -1
    assert main_window.cluster_combo.findText("Cl_A") != -1


def test_group_picked_sets_targets_and_prefills_combo(main_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1", "R2"], role="R_A"))
    main_window._set_root_sheet(root)

    main_window._on_group_picked("Role", "R_A", ["R1", "R2"])

    assert sorted(main_window._current_targets) == ["R1", "R2"]
    assert main_window.role_combo.currentText() == "R_A"
    assert main_window.stage_button.isEnabled()


def test_stage_writes_to_pending_registry(main_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    main_window._set_root_sheet(root)

    main_window._set_targets(["R1"])
    main_window.role_combo.setCurrentText("NEW")
    main_window._on_stage()

    entries = main_window._pending_registry.entries()
    assert len(entries) == 1
    assert entries[0].ref == "R1" and entries[0].field == "Role" and entries[0].new_value == "NEW"


def test_stage_with_no_target_does_nothing(main_window, tmp_path):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    main_window._set_root_sheet(root)

    main_window._set_targets([])
    main_window.role_combo.setCurrentText("NEW")
    main_window._on_stage()  # no targets -> loop body never runs

    assert main_window._pending_registry.entries() == []


def test_apply_blocked_when_kicad_running(main_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    main_window._set_root_sheet(root)
    main_window._set_targets(["R1"])
    main_window.role_combo.setCurrentText("NEW")
    main_window._on_stage()

    monkeypatch.setattr(main_window_mod.editing, "list_kicad_pids", lambda: [1234])
    write_calls = []
    monkeypatch.setattr(main_window_mod.editing, "write_files",
                        lambda *a, **k: write_calls.append(1) or ([], []))
    shown = []
    monkeypatch.setattr(main_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    main_window._on_apply()

    assert write_calls == []  # never reached the write path
    assert shown == ["info"]
    assert len(main_window._pending_registry.entries()) == 1  # still staged, nothing consumed


def test_apply_with_nothing_pending_shows_message(main_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    main_window._set_root_sheet(root)

    monkeypatch.setattr(main_window_mod.editing, "list_kicad_pids", lambda: [])
    shown = []
    monkeypatch.setattr(main_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    main_window._on_apply()
    assert shown == ["info"]


def test_apply_succeeds_writes_file_and_clears_pending(main_window, tmp_path, monkeypatch):
    root = _write_root(tmp_path, symbol_block(["R1"], role="OLD"))
    main_window._set_root_sheet(root)
    main_window._set_targets(["R1"])
    main_window.role_combo.setCurrentText("NEW")
    main_window._on_stage()

    monkeypatch.setattr(main_window_mod.editing, "list_kicad_pids", lambda: [])
    monkeypatch.setattr(main_window_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: main_window_mod.QMessageBox.StandardButton.Yes))
    shown = []
    monkeypatch.setattr(main_window_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append("info")))

    main_window._on_apply()

    assert '"Role" "NEW"' in root.read_text(encoding="utf-8")
    assert main_window._pending_registry.entries() == []
    assert shown == ["info"]
    assert Path(str(root) + ".bak").exists()
