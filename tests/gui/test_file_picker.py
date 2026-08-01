# tests/gui/test_file_picker.py
from gui import settings
from gui.docks.file_picker import FilePickerDock


def test_role_assignment_persists_and_restores(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    cells_file.write_text("{}\n", encoding="utf-8")
    extractor_file = tmp_path / "extractor.yaml"
    extractor_file.write_text("{}\n", encoding="utf-8")

    dock = FilePickerDock(main_window)
    assert all(v is None for v in dock.assigned.values())

    dock.picked_path = cells_file
    dock._assign_role("cells")
    assert dock.assigned["cells"] == cells_file

    dock.picked_path = extractor_file
    dock._assign_role("extractor")
    assert dock.assigned["extractor"] == extractor_file

    persisted = settings.load()
    assert persisted["cells_file"] == str(cells_file)
    assert persisted["extractor_file"] == str(extractor_file)

    # A fresh instance (simulating a GUI restart) should pick the roles
    # back up — restore_roles() is called by MainWindow after wiring the
    # signal listeners (see gui/main_window.py).
    restarted = FilePickerDock(main_window)
    restarted.restore_roles()
    assert restarted.assigned["cells"] == cells_file
    assert restarted.assigned["extractor"] == extractor_file
    assert restarted.assigned["placer"] is None


def test_role_signal_fires_on_assignment(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    cells_file.write_text("{}\n", encoding="utf-8")

    dock = FilePickerDock(main_window)
    received = []
    dock.cells_file_changed.connect(received.append)

    dock.picked_path = cells_file
    dock._assign_role("cells")
    assert received == [cells_file]


def test_cells_sharing_a_file_with_extractor_warns(main_window, tmp_path):
    shared = tmp_path / "shared.yaml"
    shared.write_text("{}\n", encoding="utf-8")

    dock = FilePickerDock(main_window)
    dock.picked_path = shared
    dock._assign_role("cells")
    assert dock.role_warning_label.text() == ""

    dock._assign_role("extractor")
    assert "top-level key" in dock.role_warning_label.text()

    # Reassigning Extractor elsewhere clears the warning again.
    other = tmp_path / "other.yaml"
    other.write_text("{}\n", encoding="utf-8")
    dock.picked_path = other
    dock._assign_role("extractor")
    assert dock.role_warning_label.text() == ""


def test_extractor_and_placer_sharing_a_file_is_not_a_conflict(main_window, tmp_path):
    shared = tmp_path / "root.yaml"
    shared.write_text("{}\n", encoding="utf-8")

    dock = FilePickerDock(main_window)
    dock.picked_path = shared
    dock._assign_role("extractor")
    dock._assign_role("placer")
    assert dock.role_warning_label.text() == ""
