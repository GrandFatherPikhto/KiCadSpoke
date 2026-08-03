# tests/gui/test_root_metadata.py
import yaml

from gui.docks.root_metadata import RootMetadataDock


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _read_yaml(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_no_file_picked_shows_placeholder_and_defaults(main_window):
    dock = RootMetadataDock(main_window)
    dock.set_target_file(None)
    assert "pick one" in dock.target_label.text()
    assert dock.layer_combo.currentText() == "F.Cu"
    assert dock.schematic_files_list.count() == 0
    assert dock.save_button is not None


def test_populates_widgets_from_existing_scalar_keys(main_window, tmp_path):
    path = tmp_path / "root.yaml"
    _write_yaml(path, {
        "layer": "B.Cu",
        "schematic_dir": "../sch",
        "schematic_files": ["extra1.kicad_sch", "extra2.kicad_sch"],
        "registry_path": "registries/fpga.json",
        "place_components": False,
        "skip_existing_components": True,
        "via_search_n_directions": 4,
        "cells": {"some_cell": {"components": []}},
    })
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    assert dock.layer_combo.currentText() == "B.Cu"
    assert dock._text_edits["schematic_dir"].text() == "../sch"
    assert [dock.schematic_files_list.item(i).text() for i in range(dock.schematic_files_list.count())] \
        == ["extra1.kicad_sch", "extra2.kicad_sch"]
    assert dock._text_edits["registry_path"].text() == "registries/fpga.json"
    assert dock._bool_checks["place_components"].isChecked() is False
    assert dock._bool_checks["skip_existing_components"].isChecked() is True
    assert dock._int_edits["via_search_n_directions"].text() == "4"


def test_missing_keys_show_config_defaults(main_window, tmp_path):
    path = tmp_path / "root.yaml"
    _write_yaml(path, {"cells": {}})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    assert dock.layer_combo.currentText() == "F.Cu"
    assert dock._bool_checks["place_components"].isChecked() is True
    assert dock._bool_checks["skip_existing_components"].isChecked() is False
    assert dock._float_edits["via_keepout_clearance_mm"].text() == "0.2"
    assert dock._int_edits["via_search_n_directions"].text() == "8"


def test_save_with_nothing_changed_and_nothing_present_writes_nothing(main_window, tmp_path):
    path = tmp_path / "root.yaml"
    _write_yaml(path, {"cells": {"c1": {"components": []}}})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)
    dock._on_save()

    assert _read_yaml(path) == {"cells": {"c1": {"components": []}}}
    assert "default" in dock.message_label.text()


def test_save_writes_only_changed_field_and_preserves_other_keys(main_window, tmp_path):
    path = tmp_path / "root.yaml"
    _write_yaml(path, {"cells": {"c1": {"components": []}}})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock._text_edits["schematic_dir"].setText("../../schematics")
    dock._on_save()

    data = _read_yaml(path)
    assert data["schematic_dir"] == "../../schematics"
    assert data["cells"] == {"c1": {"components": []}}
    assert "Saved" in dock.message_label.text()


def test_save_writes_already_present_key_back_even_at_default(main_window, tmp_path):
    path = tmp_path / "root.yaml"
    _write_yaml(path, {"via_search_n_directions": 4})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock._int_edits["via_search_n_directions"].setText("8")  # back to Config's own default
    dock._on_save()

    assert _read_yaml(path)["via_search_n_directions"] == 8


def test_save_rejects_non_numeric_float_field(main_window, tmp_path):
    path = tmp_path / "root.yaml"
    _write_yaml(path, {})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock._float_edits["via_keepout_clearance_mm"].setText("not-a-number")
    dock._on_save()

    assert _read_yaml(path) == {}
    assert "not a number" in dock.message_label.text()


def test_save_rejects_non_integer_int_field(main_window, tmp_path):
    path = tmp_path / "root.yaml"
    _write_yaml(path, {})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock._int_edits["via_search_n_directions"].setText("not-an-int")
    dock._on_save()

    assert _read_yaml(path) == {}
    assert "not an integer" in dock.message_label.text()


def test_save_without_a_file_picked_shows_error(main_window):
    dock = RootMetadataDock(main_window)
    dock._on_save()
    assert "Pick a file" in dock.message_label.text()


def test_schematic_files_round_trips_as_a_list(main_window, tmp_path):
    path = tmp_path / "root.yaml"
    _write_yaml(path, {})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock.schematic_files_list.addItems(["a.kicad_sch", "b.kicad_sch"])
    dock._on_save()

    assert _read_yaml(path)["schematic_files"] == ["a.kicad_sch", "b.kicad_sch"]


def test_remove_schematic_file_removes_selected_item(main_window, tmp_path):
    path = tmp_path / "root.yaml"
    _write_yaml(path, {"schematic_files": ["a.kicad_sch", "b.kicad_sch"]})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock.schematic_files_list.item(0).setSelected(True)
    dock._remove_schematic_file()

    assert [dock.schematic_files_list.item(i).text() for i in range(dock.schematic_files_list.count())] \
        == ["b.kicad_sch"]


def test_browse_dir_writes_path_relative_to_target_file(main_window, tmp_path, monkeypatch):
    target = tmp_path / "sub" / "root.yaml"
    target.parent.mkdir()
    _write_yaml(target, {})
    picked_dir = tmp_path / "sub" / "schematics"
    picked_dir.mkdir()

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getExistingDirectory",
        staticmethod(lambda *a, **k: str(picked_dir)))

    dock._browse_dir(dock._text_edits["schematic_dir"], "Schematic dir")

    assert dock._text_edits["schematic_dir"].text() == "schematics"


def test_browse_file_writes_path_relative_to_target_file(main_window, tmp_path, monkeypatch):
    target = tmp_path / "sub" / "root.yaml"
    target.parent.mkdir()
    _write_yaml(target, {})
    picked_file = tmp_path / "sub" / "registries" / "fpga.json"

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(picked_file), "")))

    dock._browse_file(dock._text_edits["registry_path"], "Registry path")

    assert dock._text_edits["registry_path"].text() == "registries/fpga.json"


def test_add_schematic_file_writes_path_relative_to_target_file(main_window, tmp_path, monkeypatch):
    target = tmp_path / "sub" / "root.yaml"
    target.parent.mkdir()
    _write_yaml(target, {})
    picked_file = tmp_path / "sub" / "extra.kicad_sch"

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **k: (str(picked_file), "")))

    dock._add_schematic_file()

    assert [dock.schematic_files_list.item(i).text() for i in range(dock.schematic_files_list.count())] \
        == ["extra.kicad_sch"]


def test_add_schematic_file_does_not_duplicate(main_window, tmp_path, monkeypatch):
    target = tmp_path / "root.yaml"
    _write_yaml(target, {"schematic_files": ["extra.kicad_sch"]})
    picked_file = tmp_path / "extra.kicad_sch"

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **k: (str(picked_file), "")))

    dock._add_schematic_file()

    assert dock.schematic_files_list.count() == 1


def test_browse_without_a_file_picked_shows_error(main_window):
    dock = RootMetadataDock(main_window)
    dock._browse_dir(dock._text_edits["schematic_dir"], "Schematic dir")
    assert "Pick a file" in dock.message_label.text()
