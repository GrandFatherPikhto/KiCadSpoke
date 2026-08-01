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
    dock.set_selected_cell("pi_filter")  # Cell picking now lives in CellListDock, see test_cell_list.py
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
    dock._param_edits["PWR_IN"].setCurrentText("+3V3_CH2")
    dock._param_edits["PWR_OUT"].setCurrentText("+3V3_CH2_DIRTY")

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
    dock.anchor_role_edit.setCurrentText("SOME_ROLE")

    assert dock._build_entry_dict() is None
    assert "mutually exclusive" in dock.message_label.text()


def test_anchor_role_with_pad_and_shift(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.cluster_edit.setText("X")
    dock.origin_mode_combo.setCurrentIndex(1)
    dock.anchor_role_edit.setCurrentText("SOME_ROLE")
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
    dock._param_edits["PWR_IN"].setCurrentText("+3V3_CH2")
    dock._param_edits["PWR_OUT"].setCurrentText("+3V3_CH2_DIRTY")

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


def test_load_placement_round_trips_absolute_xy(main_window, tmp_path):
    """Reverse of _build_entry_dict — PlacerListDock feeds a saved
    clone_placement dict straight back in via load_placement()."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {
        "name": "Channel_2_PI_Filter", "cell": "pi_filter", "xy": [10.5, -3.2],
        "params": {"PWR_IN": "+3V3_CH2", "PWR_OUT": "+3V3_CH2_DIRTY"},
    }

    dock.load_placement(entry)

    assert dock.cluster_edit.text() == "Channel_2_PI_Filter"
    assert dock._selected_cell == "pi_filter"
    assert dock.origin_mode_combo.currentIndex() == 0
    assert dock.x_edit.text() == "10.5"
    assert dock.y_edit.text() == "-3.2"
    assert dock._param_edits["PWR_IN"].currentText() == "+3V3_CH2"
    assert dock._param_edits["PWR_OUT"].currentText() == "+3V3_CH2_DIRTY"


def test_load_placement_round_trips_anchor_mode(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {
        "name": "X", "cell": "pi_filter", "xy": [2.0, 0.0],
        "anchor_role": "SOME_ROLE", "anchor_pad": "1", "anchor_cluster": "Channel_1",
    }

    dock.load_placement(entry)

    assert dock.origin_mode_combo.currentIndex() == 1
    assert dock.anchor_role_edit.currentText() == "SOME_ROLE"
    assert dock.anchor_pad_edit.text() == "1"
    assert dock.anchor_cluster_edit.currentText() == "Channel_1"
    assert dock.shift_x_edit.text() == "2.0"
    assert dock.shift_y_edit.text() == "0.0"
    # Round-trips back through _build_entry_dict without loss.
    assert dock._build_entry_dict() == entry


def test_load_placement_round_trips_point_mode(main_window, tmp_path):
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    entry = {"name": "X", "cell": "pi_filter", "xy": [1.0, 2.0], "anchor_point": "origin_point"}

    dock.load_placement(entry)

    assert dock.origin_mode_combo.currentIndex() == 2
    assert dock.point_edit.text() == "origin_point"
    assert dock.shift_x_edit.text() == "1.0"
    assert dock.shift_y_edit.text() == "2.0"


class _FakeNet:
    def __init__(self, name):
        self.name = name


class _FakeNetAdapter:
    def __init__(self, nets):
        self._nets = nets

    def get_all_nets(self):
        return self._nets


class _FakeNetBoard:
    def __init__(self, nets):
        self.adapter = _FakeNetAdapter(nets)


def test_refresh_known_nets_populates_param_combos(main_window, tmp_path):
    """Requested live 2026-08-02: "сети стоит сделать выпадашками
    (комбобоксами с поиском)" — Params comboboxes should list real net
    names from the board, not stay free-text fields."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    board = _FakeNetBoard([_FakeNet("+3V3"), _FakeNet("GND"), _FakeNet("")])

    dock.refresh_known_nets(board)

    assert dock._known_nets == ["+3V3", "GND"]  # sorted, blank net dropped
    combo = dock._param_edits["PWR_IN"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["+3V3", "GND"]
    assert combo.isEditable()  # still accepts a literal not in the list


def test_rebuilt_param_rows_use_cached_known_nets(main_window, tmp_path):
    """A newly-discovered param row (picking a Cell after nets were already
    fetched) must not have to wait for the next ~2s poll tick to show the
    net list — refresh_known_nets() caches on self for this reason."""
    dock, _, _ = _make_cell_and_dock(main_window, tmp_path)
    dock.refresh_known_nets(_FakeNetBoard([_FakeNet("+3V3"), _FakeNet("GND")]))

    dock.set_selected_cell("pi_filter")  # forces _rebuild_param_rows again

    combo = dock._param_edits["PWR_IN"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["+3V3", "GND"]


def test_refresh_known_roles_populates_from_snapshot(main_window):
    """1.2 — refresh_known_roles() takes the already-built snapshot (the
    cached BoardConnection.snapshot) instead of calling board.select()
    itself, so a manual refresh builds the full snapshot exactly once."""
    class _Row:
        def __init__(self, ref, role, cluster):
            self.ref = ref
            self.role = role
            self.cluster = cluster

    dock = PlacerDock(main_window)
    snapshot = [_Row("R1", "ROLE_A", "C1"),
                _Row("R2", "ROLE_A", "C2"),
                _Row("R3", "", "C1")]  # blank role dropped, cluster kept

    dock.refresh_known_roles(snapshot)

    roles = [dock.anchor_role_edit.itemText(i) for i in range(dock.anchor_role_edit.count())]
    clusters = [dock.anchor_cluster_edit.itemText(i) for i in range(dock.anchor_cluster_edit.count())]
    assert roles == ["ROLE_A"]
    assert clusters == ["C1", "C2"]
