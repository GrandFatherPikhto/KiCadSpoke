# tests/gui/test_extract_dock.py
import yaml
from PyQt6.QtCore import Qt

import gui.docks.extract as extract_mod
from gui.docks.extract import ExtractDock


class FakeSelected:
    def __init__(self, ref, role, cluster, nets):
        self.ref, self.role, self.cluster, self.nets = ref, role, cluster, nets


class FakeAdapter:
    pass


class FakeBoard:
    adapter = FakeAdapter()


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _fake_extract(adapter, name, params=None, items=None, annotations=None, **kwargs):
    return {name: {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}}


def test_cluster_slug_default_when_nothing_matches(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "PWR/DAC0", {"1": "+3V3"})])
    assert dock.name_edit.text() == "pwr_dac0"


def test_cluster_slug_does_not_stomp_manual_typing(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.name_edit.setText("my_custom_name")
    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "OTHER/CLUSTER", {"1": "+3V3"})])
    assert dock.name_edit.text() == "my_custom_name"


def test_existing_cell_key_beats_raw_cluster_slug(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {
        "existing_manual_name": {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"},
    })
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "Existing Manual Name", {"1": "+3V3"})])
    assert dock.name_edit.text() == "existing_manual_name"


def test_clicking_profile_pulls_aliases_role_and_origin(main_window, tmp_path):
    """Reproduces this project's own real data shape (profile key !=
    cell name, Cluster name that doesn't slugify to match either one) —
    found live 2026-08-01 that this is exactly why the cluster auto-match
    path never fires on the real board, and clicking is the path that
    actually matters."""
    cells_dir = tmp_path / "templates"
    cells_dir.mkdir()
    cells_file = cells_dir / "test.yaml"
    _write_yaml(cells_file, {"2v5_adj_pi_filter": {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}})

    extractor_file = tmp_path / "test_extract.yaml"
    _write_yaml(extractor_file, {
        "extract_profiles": {
            "n2v5_adj_pi_filter": {
                "output": "templates/test.yaml",
                "name": "2v5_adj_pi_filter",
                "params": {"PWR_OUT": "-2V5", "PWR_IN": "-2V5_DIRTY"},
                "origin_by_component_role": "C_IN_BYPASS",
                "origin_by_component_pad": "1",
            }
        }
    })

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)

    sel = [
        FakeSelected("C22", "C_OUT_BULK", "Out_Pi_Filter_N2V5", {"1": "-2V5", "2": "GND"}),
        FakeSelected("C26", "C_OUT_BYPASS", "Out_Pi_Filter_N2V5", {"1": "-2V5", "2": "GND"}),
        FakeSelected("C19", "C_IN_BYPASS", "Out_Pi_Filter_N2V5", {"1": "-2V5_DIRTY", "2": "GND"}),
        FakeSelected("FB6", "PI_FILTER_FB", "Out_Pi_Filter_N2V5", {"1": "-2V5", "2": "-2V5_DIRTY"}),
    ]
    dock.set_board_selection([], sel)

    # Cluster slug ("out_pi_filter_n2v5") matches neither cell nor profile
    # key -> Cell name only got the raw-slug fallback, Profile key (which
    # has no such fallback) stayed empty; confirming the auto-match-by-key
    # path is a no-op here before the click.
    assert dock.name_edit.text() == "out_pi_filter_n2v5"
    assert dock.profile_key_edit.text() == ""

    item = dock.profiles_list.findItems("n2v5_adj_pi_filter", Qt.MatchFlag.MatchExactly)[0]
    dock.profiles_list.itemClicked.emit(item)

    assert dock.profile_key_edit.text() == "n2v5_adj_pi_filter"
    assert dock._net_alias_edits["-2V5"].text() == "PWR_OUT"
    assert dock._net_alias_edits["-2V5_DIRTY"].text() == "PWR_IN"
    assert dock.origin_mode_combo.currentIndex() == 1
    assert dock.origin_role_combo.currentText() == "C_IN_BYPASS"
    assert dock.origin_pad_edit.text() == "1"

    # FB6/PI_FILTER_FB sits on both aliased nets -> ambiguous row appears,
    # but this profile predates net_template_role, so nothing to pull:
    # stays unresolved, requiring one manual pick.
    assert "PI_FILTER_FB" in dock._net_template_role_edits
    assert dock._net_template_role_edits["PI_FILTER_FB"].currentText() == ""


def test_clicking_cell_cross_references_matching_profile(main_window, tmp_path):
    cells_dir = tmp_path / "templates"
    cells_dir.mkdir()
    cells_file = cells_dir / "test.yaml"
    _write_yaml(cells_file, {"2v5_adj_pi_filter": {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}})

    extractor_file = tmp_path / "test_extract.yaml"
    _write_yaml(extractor_file, {
        "extract_profiles": {
            "n2v5_adj_pi_filter": {
                "output": "templates/test.yaml",
                "name": "2v5_adj_pi_filter",
                "params": {"PWR_OUT": "-2V5"},
            }
        }
    })

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)
    dock.set_board_selection([], [FakeSelected("C22", "C_OUT_BULK", "Anything", {"1": "-2V5"})])

    item = dock.cells_list.findItems("2v5_adj_pi_filter", Qt.MatchFlag.MatchExactly)[0]
    dock.cells_list.itemClicked.emit(item)

    assert dock.name_edit.text() == "2v5_adj_pi_filter"
    assert dock.profile_key_edit.text() == "n2v5_adj_pi_filter"
    assert dock._net_alias_edits["-2V5"].text() == "PWR_OUT"


def test_net_alias_positional_fallback_on_rail_swap(main_window, tmp_path):
    """A profile's params recorded against one rail ('+2V5') should still
    populate the alias rows for an analogous selection on a different
    rail ('-2V5') — no literal in common, falls back to declared order."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    extractor_file = tmp_path / "extractor.yaml"
    _write_yaml(extractor_file, {
        "extract_profiles": {
            "n2v5_adj_pi_filter": {
                "output": "cells.yaml",
                "params": {"PWR_IN": "+2V5", "PWR_OUT": "+2V5_DIRTY"},
            }
        }
    })

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)
    dock.set_board_selection([], [
        FakeSelected("D1", "SOME_ROLE", "X", {"1": "-2V5", "2": "-2V5_DIRTY", "3": "GND"}),
    ])

    item = dock.profiles_list.findItems("n2v5_adj_pi_filter", Qt.MatchFlag.MatchExactly)[0]
    dock.profiles_list.itemClicked.emit(item)

    assert dock._net_alias_edits["-2V5"].text() == "PWR_IN"
    assert dock._net_alias_edits["-2V5_DIRTY"].text() == "PWR_OUT"
    assert dock._net_alias_edits["GND"].text() == ""


def test_net_template_role_blocks_extraction_until_resolved(main_window, tmp_path, monkeypatch):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.set_board_selection([], [FakeSelected("FB6", "PI_FILTER_FB", "X", {"1": "-2V5", "2": "-2V5_DIRTY"})])
    dock._net_alias_edits["-2V5"].setText("PWR_IN")
    dock._net_alias_edits["-2V5_DIRTY"].setText("PWR_OUT")
    assert "PI_FILTER_FB" in dock._net_template_role_edits

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)
    main_window.connection.board = FakeBoard()

    dock.name_edit.setText("n2v5_adj_pi_filter")
    dock._raw_items = [object()]
    dock._on_extract()
    assert "PI_FILTER_FB" in dock.message_label.text()
    assert yaml.safe_load(cells_file.read_text()) in (None, {})

    dock._net_template_role_edits["PI_FILTER_FB"].setCurrentText("-2V5")
    dock._on_extract()
    saved = yaml.safe_load(cells_file.read_text())
    assert "n2v5_adj_pi_filter" in saved


def test_placer_gets_cell_files_and_include_entries_deduped(main_window, tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    cells_file = templates_dir / "test.yaml"
    _write_yaml(cells_file, {})
    extractor_file = tmp_path / "extracts.yaml"
    _write_yaml(extractor_file, {})
    placer_file = tmp_path / "root.yaml"
    _write_yaml(placer_file, {"clone_placements": []})

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)
    dock.set_placer_file(placer_file)

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)
    main_window.connection.board = FakeBoard()

    dock.name_edit.setText("some_cell")
    dock.save_profile_checkbox.setChecked(True)
    dock.profile_key_edit.setText("some_profile")
    dock._raw_items = [object()]
    dock._on_extract()

    placer_data = yaml.safe_load(placer_file.read_text())
    assert placer_data["cell_files"] == ["templates/test.yaml"]
    assert placer_data["include"] == ["extracts.yaml"]
    assert placer_data["clone_placements"] == []  # untouched, not overwritten

    # A second extraction under a different name must not duplicate entries.
    dock._last_autofill_key = None
    dock.name_edit.setText("another_cell")
    dock.profile_key_edit.setText("another_profile")
    dock._on_extract()

    placer_data2 = yaml.safe_load(placer_file.read_text())
    assert placer_data2["cell_files"] == ["templates/test.yaml"]
    assert placer_data2["include"] == ["extracts.yaml"]
