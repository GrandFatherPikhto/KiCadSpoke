# tests/gui/test_settings.py
"""Phase 4.1 — the Settings class (pointwise get/set + atomic write) that
replaces the scattered load() → mutate → save() pattern in the docks.

The autouse `isolated_settings` fixture (tests/gui/conftest.py) redirects
gui.settings.SETTINGS_PATH to a throwaway file for every test here, so the
module-level `state` singleton and the backward-compatible load()/save()
all operate on tmp files — nothing can touch the developer's real
gui/gui_state.json."""
import json

import pytest

from gui import settings
from gui.settings import Settings


# ── pointwise get/set ─────────────────────────────────────────────────────

def test_get_returns_default_when_key_missing(tmp_path):
    s = Settings(tmp_path / "gui_state.json")
    assert s.get("tree_group_by", 0) == 0
    assert s.get("window_geometry") is None


def test_set_then_get_round_trips(tmp_path):
    path = tmp_path / "gui_state.json"
    s = Settings(path)
    s.set("tree_group_by", 1)
    s.set("tray_enabled", True)
    assert s.get("tree_group_by") == 1
    assert s.get("tray_enabled") is True


def test_set_merges_with_existing_keys_across_instances(tmp_path):
    """A fresh Settings instance re-reads the file, so one component's key
    is never clobbered by another component writing its own key — the same
    merge semantics the old load() → mutate → save() pattern gave us."""
    path = tmp_path / "gui_state.json"
    Settings(path).set("window_geometry", {"x": 1, "y": 2})
    Settings(path).set("tray_enabled", False)  # independent instance
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["window_geometry"] == {"x": 1, "y": 2}
    assert data["tray_enabled"] is False


# ── atomic write ──────────────────────────────────────────────────────────

def test_set_writes_valid_json_with_no_temp_leftover(tmp_path):
    path = tmp_path / "gui_state.json"
    Settings(path).set("root_dir", "C:/boards")
    assert path.exists()
    assert not (tmp_path / "gui_state.json.tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"root_dir": "C:/boards"}


def test_set_replaces_a_leftover_temp_file(tmp_path):
    path = tmp_path / "gui_state.json"
    tmp = tmp_path / "gui_state.json.tmp"
    tmp.write_text("stale", encoding="utf-8")  # leftover from a past crash
    Settings(path).set("k", "v")
    assert json.loads(path.read_text(encoding="utf-8")) == {"k": "v"}
    assert not tmp.exists()


def test_get_default_on_corrupt_file_and_set_recovers(tmp_path):
    path = tmp_path / "gui_state.json"
    path.write_text("{ not json", encoding="utf-8")
    s = Settings(path)
    assert s.get("any", "fallback") == "fallback"
    s.set("k", 1)  # write must recover from the corrupt file
    assert s.get("k") == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"k": 1}


# ── legacy gui_state.json compatibility ──────────────────────────────────

def test_legacy_plain_json_file_reads_unchanged(tmp_path):
    """An existing gui_state.json (a plain dict written by the old save())
    must keep working — 4.1 changes the API, not the on-disk format."""
    legacy = {
        "window_geometry": {"x": 0, "y": 0, "width": 800, "height": 600},
        "tree_group_by": 1,
        "cells_file": "C:/boards/cells.yaml",
    }
    path = tmp_path / "gui_state.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    s = Settings(path)
    assert s.get("window_geometry")["width"] == 800
    assert s.get("tree_group_by") == 1
    assert s.get("cells_file") == "C:/boards/cells.yaml"
    assert s.get("missing", "d") == "d"


# ── module API + singleton ───────────────────────────────────────────────

def test_module_load_save_backward_compat():
    """settings.load()/save() (the whole-dict API tests seed/assert with)
    still work and stay in sync with the Settings singleton."""
    settings.save({"tree_group_by": 1})
    assert settings.load() == {"tree_group_by": 1}
    assert settings.state.get("tree_group_by") == 1


def test_state_singleton_merges_with_module_save():
    settings.save({"cells_file": "a.yaml"})
    settings.state.set("root_dir", "C:/boards")
    data = settings.load()
    assert data["cells_file"] == "a.yaml"
    assert data["root_dir"] == "C:/boards"


def test_state_singleton_resolves_monkeypatched_path_at_call_time(monkeypatch, tmp_path):
    """state is constructed at import with path=None, so it must resolve
    SETTINGS_PATH at call time — exactly what the conftest's
    isolated_settings monkeypatch relies on (it patches the attribute after
    this module is imported)."""
    s = Settings()  # constructed BEFORE the monkeypatch, like the singleton
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "gui_state.json")
    s.set("k", "v")
    assert s.get("k") == "v"
    assert (tmp_path / "gui_state.json").exists()


def test_state_singleton_is_a_settings_instance():
    assert isinstance(settings.state, Settings)
    settings.state.set("smoke", 42)
    assert settings.state.get("smoke") == 42
