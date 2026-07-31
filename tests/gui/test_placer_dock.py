# tests/gui/test_placer_dock.py
"""
PlacerDock tests are deliberately headless AND board-mutation-free:
_on_redraw()'s real job is moving real footprints on a live board, which
these tests must never do on their own. ApplyPipeline/PlacementPlanner/
load_config are monkeypatched with fakes that only check what PlacerDock
PASSES them (config_path, only=, and — most importantly — that OTHER
already-saved clone_placements survive into the config handed to the
pipeline, see test_redraw_preserves_other_placements_for_registry_safety).
Actually invoking the real pipeline against a live board is left to
manual verification against KiCad, same as every other dock this session.
"""
import yaml

import gui.docks.placer as placer_mod
from gui.docks.placer import PlacerDock
from kicadstamp.config import Cell, Config, RuntimeContext, TemplateComponentSlot, _load_clone_placement


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _make_cell_and_dock(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {
        "pi_filter": {
            "components": [{"role": "C_IN", "offset_along_mm": 0, "offset_across_mm": 0,
                             "angle_deg": 0, "net_template": "{PWR_IN}"}],
            "vias": [{"offset_along_mm": 1, "offset_across_mm": 1, "net": "{PWR_OUT}",
                      "drill_mm": 0.3, "diameter_mm": 0.6}],
            "tracks": [],
            "layer": "F.Cu",
        }
    })
    placer_file = tmp_path / "root.yaml"
    _write_yaml(placer_file, {"clone_placements": []})

    dock = PlacerDock(main_window)
    dock.set_cells_file(cells_file)
    dock.set_placer_file(placer_file)
    dock.cells_list.itemClicked.emit(dock.cells_list.item(0))
    return dock, cells_file, placer_file


def test_cell_click_discovers_placeholders(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    assert dock._selected_cell == "pi_filter"
    assert sorted(dock._param_edits.keys()) == ["PWR_IN", "PWR_OUT"]


def test_build_entry_dict_absolute_xy_round_trips_through_loader(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setText("Channel_2_PI_Filter")
    dock.x_edit.setText("10.5")
    dock.y_edit.setText("-3.2")
    dock._param_edits["PWR_IN"].setText("+3V3_CH2")
    dock._param_edits["PWR_OUT"].setText("+3V3_CH2_DIRTY")

    entry = dock._build_entry_dict()
    assert entry == {
        "name": "Channel_2_PI_Filter", "cell": "pi_filter", "xy": [10.5, -3.2],
        "params": {"PWR_IN": "+3V3_CH2", "PWR_OUT": "+3V3_CH2_DIRTY"},
    }
    cp = _load_clone_placement(entry)  # must validate against the real backend loader
    assert cp.name == "Channel_2_PI_Filter"
    assert cp.xy == (10.5, -3.2)


def test_anchor_ref_and_role_together_is_blocked(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setText("X")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_ref_edit.setText("U1")
    dock.anchor_role_edit.setText("SOME_ROLE")

    assert dock._build_entry_dict() is None
    assert "mutually exclusive" in dock.message_label.text()


def test_anchor_role_with_pad_and_shift(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setText("X")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_role_edit.setText("SOME_ROLE")
    dock.anchor_pad_edit.setText("1")
    dock.shift_x_edit.setText("2")
    dock.shift_y_edit.setText("0")

    entry = dock._build_entry_dict()
    assert entry["anchor_role"] == "SOME_ROLE"
    assert entry["anchor_pad"] == "1"
    assert entry["xy"] == [2.0, 0.0]
    cp = _load_clone_placement(entry)  # validates anchor_role/anchor_pad combination
    assert cp.anchor_role == "SOME_ROLE"
    assert cp.anchor_pad == "1"


def test_point_mode_requires_a_name(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setText("X")
    dock.origin_mode_combo.setCurrentIndex(2)

    assert dock._build_entry_dict() is None
    assert "name is required" in dock.message_label.text()

    dock.point_edit.setText("origin_point")
    entry = dock._build_entry_dict()
    assert entry["anchor_point"] == "origin_point"


def test_save_upserts_by_name_without_duplicating(main_window, tmp_path):
    dock, _, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setText("Channel_2_PI_Filter")
    dock.x_edit.setText("1")
    dock.y_edit.setText("2")
    entry = dock._build_entry_dict()

    overwritten1 = dock._upsert_clone_placement(placer_file, entry)
    assert overwritten1 is False
    saved = yaml.safe_load(placer_file.read_text())
    assert len(saved["clone_placements"]) == 1

    overwritten2 = dock._upsert_clone_placement(placer_file, entry)
    assert overwritten2 is True
    saved2 = yaml.safe_load(placer_file.read_text())
    assert len(saved2["clone_placements"]) == 1  # no duplicate on the same name

    other = dict(entry, name="Channel_3_PI_Filter")
    dock._upsert_clone_placement(placer_file, other)
    saved3 = yaml.safe_load(placer_file.read_text())
    assert sorted(e["name"] for e in saved3["clone_placements"]) == [
        "Channel_2_PI_Filter", "Channel_3_PI_Filter"]


def test_redraw_requires_cell_reachable_via_placer_config(main_window, tmp_path, monkeypatch):
    """The Cell must actually be loadable FROM the Placer file's own
    cell_files: wiring (load_config's cfg.cells) — picking a cell name in
    the list alone isn't enough if cell_files: was never pointed at it."""
    dock, cells_file, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setText("Channel_2_PI_Filter")
    dock.x_edit.setText("1")
    dock.y_edit.setText("2")

    monkeypatch.setattr(placer_mod, "load_config",
                         lambda path: (Config(), RuntimeContext()))  # cells: empty -> cell unreachable

    dock._on_redraw()
    assert "cell_files" in dock.message_label.text()


def test_redraw_preserves_other_placements_for_registry_safety(main_window, tmp_path, monkeypatch):
    """The single most important correctness property here: Redraw must
    load the REAL config (with every other already-saved clone_placement
    intact) and only narrow EXECUTION via only=, never build a config that
    looks like every other placement no longer exists — see
    PlacementRegistry.reconcile()'s known_anchor_ids protection
    (kicadstamp/registry.py). A synthetic single-placement config here
    would make Redraw silently prune everyone else's vias/tracks."""
    dock, cells_file, placer_file = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setText("Channel_2_PI_Filter")
    dock.x_edit.setText("10")
    dock.y_edit.setText("5")
    dock._param_edits["PWR_IN"].setText("+3V3_CH2")
    dock._param_edits["PWR_OUT"].setText("+3V3_CH2_DIRTY")

    pre_existing = _load_clone_placement({"name": "OTHER_PLACEMENT", "cell": "pi_filter", "xy": [0, 0]})
    fake_cfg = Config(
        cells={"pi_filter": Cell(name="pi_filter", vias=[], tracks=[], clone_placements=[], components=[
            TemplateComponentSlot(role="C_IN", offset_along_mm=0, offset_across_mm=0, angle_deg=0),
        ])},
        clone_placements=[pre_existing],
    )
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(placer_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    pipeline_calls = []

    class _FakeItem:
        def __init__(self, obj):
            self.kind = "clone"
            self.obj = obj

    class _FakeMove:
        def __init__(self, ref):
            self.ref = ref

    class _FakeFootprint:
        def __init__(self, ref):
            self.ref = ref

    class _FakeAdapter:
        def __init__(self):
            self.field_writes = None

        def get_footprint(self, ref):
            return _FakeFootprint(ref)

        def set_field_values_bulk(self, updates, description):
            self.field_writes = [(fp.ref, field, value) for fp, field, value in updates]

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg, preloaded_ctx, only, dry_run):
            pipeline_calls.append({"config_path": config_path, "cfg": preloaded_cfg, "only": only})
            self.cfg = preloaded_cfg
            self.adapter = _FakeAdapter()
            my_placement = next(c for c in preloaded_cfg.clone_placements if c.name in only)
            self.items = [_FakeItem(pre_existing), _FakeItem(my_placement)]

        def run(self):
            pass

    class _FakePlanner:
        def __init__(self, adapter, cfg, sheet_names=None):
            pass

        def begin_planning(self):
            pass

        def plan_item(self, item):
            return [_FakeMove("U5")] if item.obj.name == "Channel_2_PI_Filter" else [_FakeMove("U1")]

    monkeypatch.setattr(placer_mod, "ApplyPipeline", _FakePipeline)
    monkeypatch.setattr(placer_mod, "PlacementPlanner", _FakePlanner)

    dock._on_redraw()

    assert pipeline_calls[-1]["only"] == ["Channel_2_PI_Filter"]
    assert pipeline_calls[-1]["config_path"] == str(placer_file)
    used_cfg = pipeline_calls[-1]["cfg"]
    names = [c.name for c in used_cfg.clone_placements]
    assert "OTHER_PLACEMENT" in names  # not dropped -> registry-protected
    assert names.count("Channel_2_PI_Filter") == 1  # replaced, not duplicated
    assert "Placed" in dock.message_label.text()
    assert "1 component(s) tagged Cluster" in dock.message_label.text()
