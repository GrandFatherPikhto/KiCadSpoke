# tests/gui/test_config_tree.py
"""Tests for ConfigTreeDock (gui/docks/config_tree.py) — one tree mirroring
the actual include: file graph from a single root file (2026-08-03, GUI
tree roadmap Этап 1/2, corrected same day from an earlier flat,
non-recursive version — see handoff_2026_08_03_gui_tree_risks_resolved.md
and the config-architecture-brainstorm memory)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml
from PyQt6.QtWidgets import QMessageBox

import gui.docks.config_tree as config_tree_mod
from gui import settings
from gui.docks.config_tree import ConfigTreeDock

MINIMAL_CELL = """
cells:
  one_role:
    components:
      - role: THE_ROLE
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
"""


def _find(item, text):
    for i in range(item.childCount()):
        child = item.child(i)
        if child.text(0) == text:
            return child
    raise AssertionError(f"no child {text!r} under {item.text(0)!r}")


def test_root_file_own_sections_shown_directly(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    assert root_item.text(0) == "root.yaml"
    cells = _find(root_item, "Cells")
    assert cells.child(0).text(0) == "one_role"


def test_included_file_becomes_a_nested_file_node_not_merged_in(main_window, tmp_path):
    (tmp_path / "sub.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    assert root_item.childCount() == 1  # nothing of its own, just sub.yaml
    sub_item = root_item.child(0)
    assert sub_item.text(0) == "sub.yaml"
    assert _find(sub_item, "Cells").child(0).text(0) == "one_role"


def test_nested_includes_recurse(main_window, tmp_path):
    (tmp_path / "c.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    (tmp_path / "b.yaml").write_text("include:\n  - c.yaml\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - b.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    b_item = dock.tree.topLevelItem(0).child(0)
    assert b_item.text(0) == "b.yaml"
    c_item = b_item.child(0)
    assert c_item.text(0) == "c.yaml"
    assert _find(c_item, "Cells").child(0).text(0) == "one_role"


def test_clicking_a_cell_leaf_fires_cell_picked(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Cells").child(0)
    picked = []
    dock.cell_picked.connect(picked.append)
    dock.tree.itemClicked.emit(leaf, 0)

    assert picked == ["one_role"]


def test_clicking_a_placement_leaf_fires_full_dict(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(
        "clone_placements:\n  - name: spoke_1\n    cell: ldo_adj\n    xy: [0, 0]\n",
        encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Clone placements").child(0)
    picked = []
    dock.placement_picked.connect(picked.append)
    dock.tree.itemClicked.emit(leaf, 0)

    assert picked == [{"name": "spoke_1", "cell": "ldo_adj", "xy": [0, 0]}]


def test_clicking_an_extract_profile_leaf_fires_profile_picked(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("extract_profiles:\n  alpha:\n    params: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Extract profiles").child(0)
    picked = []
    dock.profile_picked.connect(picked.append)
    dock.tree.itemClicked.emit(leaf, 0)

    assert picked == ["alpha"]


def test_clicking_a_rules_leaf_fires_no_signal_no_form_yet(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(
        "rules:\n  - net: '+3V3'\n    anchor_ref: U1\n    spokes: []\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    leaf = _find(dock.tree.topLevelItem(0), "Rules").child(0)
    assert leaf.text(0) == "+3V3"
    picked = []
    dock.cell_picked.connect(picked.append)
    dock.placement_picked.connect(picked.append)
    dock.profile_picked.connect(picked.append)
    dock.tree.itemClicked.emit(leaf, 0)

    assert picked == []


def test_clicking_a_file_or_category_header_fires_no_signal(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    picked = []
    dock.cell_picked.connect(picked.append)
    dock.tree.itemClicked.emit(root_item, 0)  # file header
    dock.tree.itemClicked.emit(_find(root_item, "Cells"), 0)  # category header

    assert picked == []


def test_no_root_file_assigned_yields_an_empty_tree(main_window):
    dock = ConfigTreeDock(main_window)
    assert dock.tree.topLevelItemCount() == 0


def test_refresh_picks_up_a_change_made_on_disk(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    root_item = dock.tree.topLevelItem(0)
    assert root_item.childCount() == 0  # empty cells: section, nothing shown

    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock.refresh()

    root_item = dock.tree.topLevelItem(0)
    assert _find(root_item, "Cells").child(0).text(0) == "one_role"


def test_a_true_cycle_shows_as_a_single_error_item_not_a_crash(main_window, tmp_path):
    (tmp_path / "a.yaml").write_text("include:\n  - b.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("include:\n  - a.yaml\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - a.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    assert dock.tree.topLevelItemCount() == 1
    assert "cycle detected" in dock.tree.topLevelItem(0).text(0)


# ── Context menu (2026-08-03) — file-level actions, same set regardless ──
# of whether the file header, a category, or a leaf was right-clicked. ────

def test_file_context_resolves_from_a_leaf_and_a_category(main_window, tmp_path):
    """_file_context_for_item must find the same file whether the click
    landed on the file header, a category under it, or a specific leaf —
    Denis: "Если выбран файл или его десцендант..." """
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    root_item = dock.tree.topLevelItem(0)
    cells_category = _find(root_item, "Cells")
    leaf = cells_category.child(0)

    for item in (root_item, cells_category, leaf):
        file_path, parent_path = dock._file_context_for_item(item)
        assert file_path == root.resolve()
        assert parent_path is None  # root has no parent


def test_file_context_for_a_nested_included_file(main_window, tmp_path):
    (tmp_path / "sub.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    sub_item = dock.tree.topLevelItem(0).child(0)
    file_path, parent_path = dock._file_context_for_item(sub_item)
    assert file_path == (tmp_path / "sub.yaml").resolve()
    assert parent_path == root.resolve()


def test_add_cell_writes_a_minimal_stub_and_refreshes(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("new_cell", True)))
    dock._add_cell(root)

    data = yaml.safe_load(root.read_text(encoding="utf-8"))
    assert data["cells"]["new_cell"] == {"components": []}
    assert _find(dock.tree.topLevelItem(0), "Cells").child(0).text(0) == "new_cell"


def test_add_cell_cancelled_writes_nothing(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", False)))
    dock._add_cell(root)

    assert yaml.safe_load(root.read_text(encoding="utf-8")) == {"cells": {}}


def test_rename_action_present_for_a_leaf_absent_for_a_category(main_window, tmp_path):
    """2026-08-04, Denis: "А мы можем добавить конекстное меню в конфиг
    чтобы переименовать плэейсменты, целлы, профили извлечения и т.д.?" —
    Rename only makes sense on an actual entry, not a category header or
    the file node itself."""
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    cells_category = _find(dock.tree.topLevelItem(0), "Cells")
    leaf = cells_category.child(0)

    assert leaf.data(0, config_tree_mod.Qt.ItemDataRole.UserRole)[0] == "leaf"
    assert cells_category.data(0, config_tree_mod.Qt.ItemDataRole.UserRole) is None


def test_rename_cell_updates_the_dict_key_and_refreshes_the_tree(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("renamed_cell", True)))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    dock._on_rename(root, "cells", "one_role")

    data = yaml.safe_load(root.read_text(encoding="utf-8"))
    assert "renamed_cell" in data["cells"]
    assert "one_role" not in data["cells"]
    assert _find(dock.tree.topLevelItem(0), "Cells").child(0).text(0) == "renamed_cell"


def test_rename_cell_cascades_a_reference_in_another_file(main_window, tmp_path, monkeypatch):
    (tmp_path / "cells.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text(
        "include:\n  - cells.yaml\n"
        "clone_placements:\n  - name: spoke_1\n    cell: one_role\n",
        encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("renamed_cell", True)))
    shown = []
    monkeypatch.setattr(config_tree_mod.QMessageBox, "information",
                        staticmethod(lambda self_, title, text: shown.append(text)))

    dock._on_rename(tmp_path / "cells.yaml", "cells", "one_role")

    assert yaml.safe_load(root.read_text(encoding="utf-8"))["clone_placements"][0]["cell"] == \
        "renamed_cell"
    assert len(shown) == 1 and "root.yaml" in shown[0]  # summary names the other changed file


def test_rename_declined_leaves_the_file_untouched(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", False)))

    dock._on_rename(root, "cells", "one_role")

    assert "one_role" in yaml.safe_load(root.read_text(encoding="utf-8"))["cells"]


def test_rename_collision_shows_a_warning_and_writes_nothing(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text("cells:\n  a: {}\n  b: {}\n", encoding="utf-8")
    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("b", True)))
    warned = []
    monkeypatch.setattr(config_tree_mod.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(1)))

    dock._on_rename(root, "cells", "a")

    assert warned == [1]
    assert yaml.safe_load(root.read_text(encoding="utf-8"))["cells"] == {"a": {}, "b": {}}


def test_add_thermal_via_pad_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("thermal_via_arrays: []\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_thermal_via_requested.connect(requested.append)
    dock.add_thermal_via_requested.emit(root)

    assert requested == [root]
    # nothing written — Add thermal via pad defers to ThermalViaArrayDock's
    # own Save path (2026-08-03, same reasoning as Add placer)
    assert yaml.safe_load(root.read_text(encoding="utf-8")) == {"thermal_via_arrays": []}


def test_thermal_via_leaf_click_emits_thermal_via_picked(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(
        "thermal_via_arrays:\n  - name: fpga_thermal\n    pad: '1'\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    picked = []
    dock.thermal_via_picked.connect(picked.append)
    leaf = _find(dock.tree.topLevelItem(0), "Thermal via arrays").child(0)
    dock._on_clicked(leaf, 0)

    assert picked == [{"name": "fpga_thermal", "pad": "1"}]


def test_add_point_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("points: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_point_requested.connect(requested.append)
    dock.add_point_requested.emit(root)

    assert requested == [root]
    # nothing written — Add point defers to PointsDock's own Save path,
    # same reasoning as Add thermal via pad/Add placer above.
    assert yaml.safe_load(root.read_text(encoding="utf-8")) == {"points": {}}


def test_points_leaf_click_emits_points_picked_with_the_name(main_window, tmp_path):
    """points: is a DICT section (see _entries()) — unlike
    thermal_via_picked's full-dict payload above, the click only carries
    the name; PointsDock.load_entry() re-reads the file for the data."""
    root = tmp_path / "root.yaml"
    root.write_text("points:\n  origin:\n    xy: [0, 0]\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    picked = []
    dock.points_picked.connect(picked.append)
    leaf = _find(dock.tree.topLevelItem(0), "Points").child(0)
    dock._on_clicked(leaf, 0)

    assert picked == ["origin"]


def test_add_rule_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("rules: []\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_rule_requested.connect(requested.append)
    dock.add_rule_requested.emit(root)

    assert requested == [root]
    # nothing written — Add rule defers to RuleDock's own Save path, same
    # reasoning as Add thermal via pad/Add placer/Add point above.
    assert yaml.safe_load(root.read_text(encoding="utf-8")) == {"rules": []}


def test_rule_leaf_click_emits_rule_picked(main_window, tmp_path):
    """rules: is a LIST section (see _entries()) — like thermal_via_picked,
    the payload is already the full dict."""
    root = tmp_path / "root.yaml"
    root.write_text(
        "rules:\n  - net: '+3V3'\n    anchor_role: FPGA\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    picked = []
    dock.rule_picked.connect(picked.append)
    leaf = _find(dock.tree.topLevelItem(0), "Rules").child(0)
    dock._on_clicked(leaf, 0)

    assert picked == [{"net": "+3V3", "anchor_role": "FPGA"}]


def test_add_placer_emits_request_instead_of_writing_directly(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("clone_placements: []\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    requested = []
    dock.add_placer_requested.connect(requested.append)
    dock.add_placer_requested.emit(root)

    assert requested == [root]
    # nothing written — Add placer defers to PlacerDock's own Save path
    assert yaml.safe_load(root.read_text(encoding="utf-8")) == {"clone_placements": []}


def test_add_included_file_creates_missing_file_and_wires_include(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")
    new_file = tmp_path / "power.yaml"
    assert not new_file.exists()

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(new_file), "")))
    dock._add_included_file(root)

    assert new_file.exists()
    assert yaml.safe_load(root.read_text(encoding="utf-8"))["include"] == ["power.yaml"]
    assert dock.tree.topLevelItem(0).child(0).text(0) == "power.yaml"


def test_add_included_file_rejects_a_file_with_root_only_keys(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("layer: B.Cu\ncells: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(bad_file), "")))
    monkeypatch.setattr(config_tree_mod.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    dock._add_included_file(root)

    assert "include" not in yaml.safe_load(root.read_text(encoding="utf-8"))


def test_remove_file_disables_include_after_confirmation(main_window, tmp_path, monkeypatch):
    (tmp_path / "sub.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)
    assert dock.tree.topLevelItem(0).childCount() == 1

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dock._remove_file(tmp_path / "sub.yaml", root)

    data = yaml.safe_load(root.read_text(encoding="utf-8"))
    assert data["include"] == [{"path": "sub.yaml", "enabled": False}]
    # walk_include_tree skips disabled includes -> sub.yaml no longer shown
    assert dock.tree.topLevelItem(0).childCount() == 0


def test_remove_file_declined_leaves_include_untouched(main_window, tmp_path, monkeypatch):
    (tmp_path / "sub.yaml").write_text(MINIMAL_CELL, encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    dock._remove_file(tmp_path / "sub.yaml", root)

    assert yaml.safe_load(root.read_text(encoding="utf-8"))["include"] == ["sub.yaml"]
    assert dock.tree.topLevelItem(0).childCount() == 1


def test_context_menu_has_no_remove_action_for_root(main_window, tmp_path, monkeypatch):
    """Root has no parent to remove itself from — the menu built for it
    must omit "Remove this file" entirely, not just disable it."""
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = {}
    original_add_action = config_tree_mod.QMenu.addAction

    def _record(self, text, *a, **k):
        captured.setdefault("labels", []).append(text)
        return original_add_action(self, text, *a, **k)

    monkeypatch.setattr(config_tree_mod.QMenu, "addAction", _record)

    root_item = dock.tree.topLevelItem(0)
    dock._on_context_menu(dock.tree.visualItemRect(root_item).center())

    assert "Remove this file" not in captured["labels"]
    assert "Add cell..." in captured["labels"]


# ── Open Root file + Recent (2026-08-03) — replaces FilePickerDock ───────

def test_open_root_via_dialog_sets_root_and_remembers_it(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    monkeypatch.setattr(config_tree_mod.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(root), "")))
    dock._on_open_root()

    assert dock._root_path == root
    assert dock.tree.topLevelItem(0).text(0) == "root.yaml"
    assert settings.state.get("last_root_file") == str(root)
    assert settings.state.get("recent_root_files") == [str(root)]


def test_open_root_dialog_cancelled_leaves_root_untouched(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    dock._on_open_root()

    assert dock._root_path == root


def test_new_root_creates_an_empty_file_and_opens_it(main_window, tmp_path, monkeypatch):
    new_root = tmp_path / "brand_new.yaml"
    assert not new_root.exists()

    dock = ConfigTreeDock(main_window)
    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(new_root), "")))
    dock._on_new_root()

    assert new_root.exists()
    assert yaml.safe_load(new_root.read_text(encoding="utf-8")) == {}
    assert dock._root_path == new_root
    assert dock.tree.topLevelItem(0).text(0) == "brand_new.yaml"


def test_new_root_does_not_overwrite_an_existing_file(main_window, tmp_path, monkeypatch):
    existing = tmp_path / "already_here.yaml"
    existing.write_text(MINIMAL_CELL, encoding="utf-8")
    before = existing.read_text(encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(existing), "")))
    dock._on_new_root()

    assert existing.read_text(encoding="utf-8") == before
    assert dock._root_path == existing


def test_new_root_dialog_cancelled_leaves_root_untouched(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(config_tree_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    dock._on_new_root()

    assert dock._root_path == root


def test_set_root_file_emits_root_file_changed(main_window, tmp_path):
    """root_file_changed (2026-08-05) is the signal RootMetadataDock listens
    to instead of file_selected — set_root_file() is its only source,
    unlike file_selected which fires on every plain tree click too."""
    root = tmp_path / "root.yaml"
    root.write_text("cells: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    received = []
    dock.root_file_changed.connect(received.append)

    dock.set_root_file(root)
    assert received == [root]

    dock.set_root_file(None)
    assert received == [root, None]


def test_recent_list_most_recent_first_and_deduplicated(main_window, tmp_path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("cells: {}\n", encoding="utf-8")
    b.write_text("cells: {}\n", encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(a)
    dock.set_root_file(b)
    dock.set_root_file(a)  # re-opening a must move it back to front, not duplicate

    assert settings.state.get("recent_root_files") == [str(a), str(b)]
    assert dock.recent_combo.count() == 2
    assert dock.recent_combo.itemData(0) == str(a)
    assert dock.recent_combo.itemData(1) == str(b)


def test_selecting_a_recent_entry_reopens_it(main_window, tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text(MINIMAL_CELL, encoding="utf-8")

    dock = ConfigTreeDock(main_window)
    dock.set_root_file(a)
    dock.set_root_file(None)
    assert dock.tree.topLevelItemCount() == 0

    dock._on_recent_selected(0)  # only entry: a.yaml

    assert dock._root_path == a
    assert dock.tree.topLevelItem(0).text(0) == "a.yaml"


def test_restores_last_root_file_on_construction(main_window, tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(MINIMAL_CELL, encoding="utf-8")
    settings.state.set("last_root_file", str(root))

    dock = ConfigTreeDock(main_window)

    assert dock._root_path == root
    assert dock.tree.topLevelItem(0).text(0) == "root.yaml"
