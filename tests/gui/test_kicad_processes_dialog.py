# tests/gui/test_kicad_processes_dialog.py
from PyQt6.QtWidgets import QMessageBox

import gui.kicad_processes_dialog as kicad_processes_dialog_mod
from gui.kicad_processes_dialog import KicadProcessesDialog
from kicadstamp.schematic_safety import KicadProcessInfo


def _patch_processes(monkeypatch, processes):
    monkeypatch.setattr(kicad_processes_dialog_mod, "list_kicad_processes", lambda: processes)


def test_refresh_populates_list_from_processes(qapp, monkeypatch):
    _patch_processes(monkeypatch, [
        KicadProcessInfo(pid=26736, status="Running", title="3CH-AWG-TIA - PCB Editor"),
        KicadProcessInfo(pid=36828, status="Not Responding", title=None),
    ])

    dialog = KicadProcessesDialog()

    assert dialog.list.count() == 2
    assert dialog.list.item(0).text() == "PID 26736 — Running — 3CH-AWG-TIA - PCB Editor"
    assert dialog.list.item(1).text() == "PID 36828 — Not Responding"
    assert dialog.message_label.text() == ""


def test_empty_list_shows_message(qapp, monkeypatch):
    _patch_processes(monkeypatch, [])

    dialog = KicadProcessesDialog()

    assert dialog.list.count() == 0
    assert "No KiCad process found" in dialog.message_label.text()


def test_kill_without_selection_shows_message(qapp, monkeypatch):
    _patch_processes(monkeypatch, [KicadProcessInfo(pid=1, status="Running", title=None)])
    dialog = KicadProcessesDialog()
    dialog.list.setCurrentRow(-1)

    dialog._on_kill()

    assert "Pick a process" in dialog.message_label.text()


def test_kill_declined_does_not_call_kill_kicad_process(qapp, monkeypatch):
    _patch_processes(monkeypatch, [KicadProcessInfo(pid=1, status="Running", title=None)])
    monkeypatch.setattr(kicad_processes_dialog_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    calls = []
    monkeypatch.setattr(kicad_processes_dialog_mod, "kill_kicad_process",
                        lambda pid: calls.append(pid))
    dialog = KicadProcessesDialog()
    dialog.list.setCurrentRow(0)

    dialog._on_kill()

    assert calls == []


def test_kill_confirmed_calls_kill_kicad_process_and_refreshes(qapp, monkeypatch):
    remaining = [KicadProcessInfo(pid=1, status="Running", title=None)]
    _patch_processes(monkeypatch, remaining)
    monkeypatch.setattr(kicad_processes_dialog_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    calls = []

    def _kill(pid):
        calls.append(pid)
        remaining.clear()  # simulate the process actually being gone afterwards
    monkeypatch.setattr(kicad_processes_dialog_mod, "kill_kicad_process", _kill)
    dialog = KicadProcessesDialog()
    dialog.list.setCurrentRow(0)

    dialog._on_kill()

    assert calls == [1]
    assert dialog.list.count() == 0  # refreshed after the kill


def test_kill_failure_shows_error_message(qapp, monkeypatch):
    _patch_processes(monkeypatch, [KicadProcessInfo(pid=1, status="Running", title=None)])
    monkeypatch.setattr(kicad_processes_dialog_mod.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    def _kill(pid):
        raise RuntimeError("access denied")
    monkeypatch.setattr(kicad_processes_dialog_mod, "kill_kicad_process", _kill)
    dialog = KicadProcessesDialog()
    dialog.list.setCurrentRow(0)

    dialog._on_kill()

    assert "access denied" in dialog.message_label.text()
    assert dialog.list.count() == 1  # not refreshed away on failure


def test_main_window_button_opens_the_dialog(real_main_window, monkeypatch):
    """The status-bar button (gui/main_window.py) must open a REAL
    KicadProcessesDialog, not just call list_kicad_processes()/kill directly
    — .exec() is mocked only to avoid blocking on a real modal loop."""
    opened = []
    monkeypatch.setattr(kicad_processes_dialog_mod.KicadProcessesDialog, "exec",
                        lambda self: opened.append(self) or 0)

    real_main_window.kicad_processes_button.click()

    assert len(opened) == 1
    assert isinstance(opened[0], KicadProcessesDialog)


def test_refresh_button_reloads_the_list(qapp, monkeypatch):
    processes = [KicadProcessInfo(pid=1, status="Running", title=None)]
    _patch_processes(monkeypatch, processes)
    dialog = KicadProcessesDialog()
    assert dialog.list.count() == 1

    processes.append(KicadProcessInfo(pid=2, status="Running", title=None))
    dialog.refresh_button.click()

    assert dialog.list.count() == 2
