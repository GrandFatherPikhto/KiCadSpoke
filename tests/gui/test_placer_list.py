# tests/gui/test_placer_list.py
import yaml

from gui.docks.placer_list import PlacerListDock


def _write_placer_file(path, clone_placements) -> None:
    path.write_text(yaml.dump({"clone_placements": clone_placements}, allow_unicode=True), encoding="utf-8")


def test_lists_clone_placement_names_sorted(main_window, tmp_path):
    placer_file = tmp_path / "placer.yaml"
    _write_placer_file(placer_file, [
        {"name": "Channel_2_PI_Filter", "cell": "pi_filter", "xy": [1, 2]},
        {"name": "Channel_1_PI_Filter", "cell": "pi_filter", "xy": [3, 4]},
    ])

    dock = PlacerListDock(main_window)
    dock.set_placer_file(placer_file)

    assert [dock.list.item(i).text() for i in range(dock.list.count())] == [
        "Channel_1_PI_Filter", "Channel_2_PI_Filter"]


def test_clicking_an_entry_fires_on_placement_picked_with_full_dict(main_window, tmp_path):
    placer_file = tmp_path / "placer.yaml"
    entry = {"name": "Channel_2_PI_Filter", "cell": "pi_filter", "xy": [1, 2], "rotation_deg": 90}
    _write_placer_file(placer_file, [entry])

    dock = PlacerListDock(main_window)
    dock.set_placer_file(placer_file)

    picked = []
    dock.on_placement_picked = picked.append
    dock._on_clicked(dock.list.item(0))

    assert picked == [entry]


def test_refresh_picks_up_a_placement_added_after_the_file_was_first_assigned(main_window, tmp_path):
    """PlacerDock.on_saved wires straight into this — a Save must show up
    here without the user reassigning the Placer file in Files."""
    placer_file = tmp_path / "placer.yaml"
    _write_placer_file(placer_file, [])

    dock = PlacerListDock(main_window)
    dock.set_placer_file(placer_file)
    assert dock.list.count() == 0

    _write_placer_file(placer_file, [{"name": "New_One", "cell": "pi_filter", "xy": [0, 0]}])
    dock.refresh()

    assert [dock.list.item(i).text() for i in range(dock.list.count())] == ["New_One"]


def test_no_placer_file_assigned_yields_an_empty_list(main_window):
    dock = PlacerListDock(main_window)
    dock.set_placer_file(None)
    assert dock.list.count() == 0
