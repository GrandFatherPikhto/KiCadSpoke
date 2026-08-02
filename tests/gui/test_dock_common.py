# tests/gui/test_dock_common.py
"""Regression tests for gui/docks/_common.py — the shared dock helpers
(Phase 2 of the gui/ cleanup roadmap). The read-merge-write helpers
(merge_write / add_list_entry / upsert_clone_placement) are exercised
against BOTH dispatch paths the docks rely on: YAML by default and JSON by
file extension, since the existing dock tests only ever drive them through
YAML files (test_extract_dock / test_placer_dock write .yaml fixtures)."""

import json
import logging
from pathlib import Path

import pytest
import yaml

from gui.docks._common import (ERROR_STYLE, SUCCESS_STYLE, WARN_STYLE,
                               add_list_entry, configure_searchable,
                               display_path, merge_write, set_combo_items,
                               show_message, upsert_clone_placement)


def _load(path: Path):
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(params=[".yaml", ".json"])
def config_path(tmp_path, request):
    """Same file path with either extension — exercises both the YAML and
    the JSON dispatch of the merge-write helpers."""
    return tmp_path / f"config{request.param}"


# ── merge_write ─────────────────────────────────────────────────────────

def test_merge_write_flat_preserves_other_keys(config_path):
    config_path.write_text(
        json.dumps({"old_cell": {"x": 1}}) if config_path.suffix == ".json"
        else "old_cell:\n  x: 1\n",
        encoding="utf-8")
    overwritten = merge_write(config_path, {"new_cell": {"x": 2}})
    assert overwritten is False
    data = _load(config_path)
    assert data["old_cell"] == {"x": 1}  # untouched
    assert data["new_cell"] == {"x": 2}


def test_merge_write_flat_reports_overwrite(config_path):
    config_path.write_text(
        json.dumps({"cell": {"x": 1}}) if config_path.suffix == ".json"
        else "cell:\n  x: 1\n",
        encoding="utf-8")
    assert merge_write(config_path, {"cell": {"x": 9}}) is True
    assert _load(config_path)["cell"] == {"x": 9}


def test_merge_write_section_merges_only_that_nested_dict(config_path):
    config_path.write_text(
        json.dumps({"clone_placements": [{"name": "A"}],
                    "extract_profiles": {"p1": {"a": 1}}})
        if config_path.suffix == ".json"
        else "clone_placements:\n  - name: A\nextract_profiles:\n  p1:\n    a: 1\n",
        encoding="utf-8")
    overwritten = merge_write(
        config_path, {"extract_profiles": {"p2": {"b": 2}}}, section="extract_profiles")
    assert overwritten is False
    data = _load(config_path)
    assert data["clone_placements"] == [{"name": "A"}]  # other top-level key untouched
    assert data["extract_profiles"] == {"p1": {"a": 1}, "p2": {"b": 2}}


def test_merge_write_creates_missing_file(config_path):
    assert merge_write(config_path, {"cell": {"x": 1}}) is False
    assert _load(config_path) == {"cell": {"x": 1}}


# ── add_list_entry ──────────────────────────────────────────────────────

def test_add_list_entry_appends_and_dedupes(config_path):
    config_path.write_text(
        json.dumps({"include": ["sub/a.yaml"]}) if config_path.suffix == ".json"
        else "include:\n  - sub/a.yaml\n",
        encoding="utf-8")
    # a different relative spelling resolving to the same file is a no-op
    assert add_list_entry(config_path, "include", "sub/./a.yaml") is False
    assert add_list_entry(config_path, "include", "other.yaml") is True
    assert _load(config_path)["include"] == ["sub/a.yaml", "other.yaml"]


def test_add_list_entry_refuses_non_list_section(config_path):
    config_path.write_text(
        json.dumps({"include": "not-a-list"}) if config_path.suffix == ".json"
        else "include: not-a-list\n",
        encoding="utf-8")
    with pytest.raises(OSError):
        add_list_entry(config_path, "include", "x.yaml")


# ── upsert_clone_placement ──────────────────────────────────────────────

def test_upsert_clone_placement_replaces_by_name_and_appends(config_path):
    config_path.write_text(
        json.dumps({"clone_placements": [{"name": "A", "cell": "c1"}]})
        if config_path.suffix == ".json"
        else "clone_placements:\n  - name: A\n    cell: c1\n",
        encoding="utf-8")
    assert upsert_clone_placement(config_path, {"name": "A", "cell": "c2"}) is True
    assert upsert_clone_placement(config_path, {"name": "B", "cell": "c1"}) is False
    data = _load(config_path)
    assert [e["name"] for e in data["clone_placements"]] == ["A", "B"]
    assert data["clone_placements"][0]["cell"] == "c2"  # replaced in place, not appended


def test_upsert_clone_placement_refuses_non_list(config_path):
    config_path.write_text(
        json.dumps({"clone_placements": "nope"}) if config_path.suffix == ".json"
        else "clone_placements: nope\n",
        encoding="utf-8")
    with pytest.raises(OSError):
        upsert_clone_placement(config_path, {"name": "A"})


# ── display_path ────────────────────────────────────────────────────────

def test_display_path_relative_inside_project_and_absolute_outside(tmp_path, monkeypatch):
    from gui.docks import _common
    monkeypatch.setattr(_common, "PROJECT_ROOT", tmp_path)
    inside = tmp_path / "boards" / "cell.yaml"
    inside.parent.mkdir()
    assert display_path(inside) == str(Path("boards/cell.yaml"))
    outside = tmp_path.parent / "elsewhere.yaml"
    assert display_path(outside) == str(outside)


# ── Qt widget helpers ───────────────────────────────────────────────────

def test_set_combo_items_preserves_current_text(qapp):
    from PyQt6.QtWidgets import QComboBox
    combo = QComboBox()
    combo.setEditable(True)  # docks configure combos as searchable/editable
    combo.addItems(["a", "b", "c"])
    combo.setCurrentText("b")
    set_combo_items(combo, ["x", "y"])
    assert combo.currentText() == "b"  # in-progress value survives the refresh
    assert [combo.itemText(i) for i in range(combo.count())] == ["x", "y"]


def test_configure_searchable_makes_combo_editable_noinsert(qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QComboBox, QCompleter
    combo = QComboBox()
    configure_searchable(combo)
    assert combo.isEditable() is True
    assert combo.insertPolicy() == QComboBox.InsertPolicy.NoInsert
    completer = combo.completer()
    assert completer is not None
    assert completer.completionMode() == QCompleter.CompletionMode.PopupCompletion
    assert completer.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive


def test_show_message_sets_label_and_logs_by_style(qapp, caplog):
    from PyQt6.QtWidgets import QLabel
    label = QLabel()
    dock_logger = logging.getLogger("gui.docks.test_dock_common")
    with caplog.at_level(logging.DEBUG, logger="gui.docks.test_dock_common"):
        show_message(label, "boom", ERROR_STYLE, dock_logger)
        assert label.text() == "boom"
        assert label.styleSheet() == ERROR_STYLE
        assert caplog.records[-1].levelname == "ERROR"
        show_message(label, "careful", WARN_STYLE, dock_logger)
        assert caplog.records[-1].levelname == "WARNING"
        show_message(label, "done", SUCCESS_STYLE, dock_logger)
        assert caplog.records[-1].levelname == "INFO"
        show_message(label, "", "", dock_logger)  # clears the label, no log record
        assert label.text() == ""
