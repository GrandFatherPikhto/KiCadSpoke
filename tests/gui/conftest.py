# tests/gui/conftest.py
"""
Shared fixtures for gui/ dock tests. These are the ad hoc smoke-test
scripts run by hand throughout 2026-08-01's GUI work, formalized here —
requested live: "Ты там тесты гонял по GUI. Может имеет смысл их закинуть
в tests/gui?"

Offscreen, no live KiCad connection needed (unlike tests/integration_tests,
which require a running KiCad + board — see its own conftest.py): these
exercise dock logic (autofill, persistence, validation) against synthetic
selections and throwaway files, the same way every dock was hand-verified
before being committed this session. QT_QPA_PLATFORM=offscreen is set here
(before PyQt6 is imported anywhere) so plain `pytest` works without the
caller needing to export it first.
"""
import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtWidgets import QApplication, QMainWindow

from fieldstool.gui import settings as fieldstool_settings
from gui import settings
from gui.docks.log_panel import LogDock
from gui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole test session — Qt only tolerates a
    single instance per process, so this must be session-scoped, not
    per-test."""
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Every gui.settings.load()/save() call in a test hits a throwaway
    file instead of the developer's real gui/gui_state.json — autouse so
    no test can forget it and accidentally pollute real GUI state. Also
    isolates fieldstool.gui.settings — constructing the real MainWindow
    (see real_main_window below) embeds a real fieldstool MainWindow via
    FieldsToolDock, which would otherwise touch the developer's real
    fieldstool_gui_state.json (e.g. restoring a real root_sheet path)."""
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "gui_state.json")
    monkeypatch.setattr(fieldstool_settings, "SETTINGS_PATH", tmp_path / "fieldstool_gui_state.json")


class _FakeConnection:
    board = None
    # Phase 5.1 — the embedded fieldstool window now receives this connection
    # directly (it checks connection.is_connected in _push_selection_to_board),
    # so the fake needs the same attribute the real BoardConnection exposes.
    is_connected = False
    # Phase 5.2 — held exclusively by a background long op (Extract/Redraw,
    # gui/worker.py); the main window's polling timers and the fieldstool's
    # _push_selection_to_board check it, so the fake needs the same attribute
    # the real BoardConnection exposes.
    long_op_active = False


@pytest.fixture
def main_window(qapp):
    """A real QMainWindow (docks need a QWidget parent, not a plain
    object) with a stubbed .connection.board — enough for the docks under
    test here, which only ever check `connection.board is None` before
    touching the live board, and have that board's methods monkeypatched
    per-test where an actual write path needs exercising."""
    window = QMainWindow()
    window.connection = _FakeConnection()
    return window


@pytest.fixture
def real_main_window(qapp):
    """The real gui.main_window.MainWindow — unlike `main_window` above
    (a bare QMainWindow stub), this is needed for anything that exercises
    tray/closeEvent/single-instance/fieldstool-dock-embedding logic, all of
    which live on the real class. A tiny timeout_ms keeps construction fast
    (no live KiCad here — Board.connect() fails quickly and _poll() just
    records "not connected"). Stops all four QTimers on teardown (this
    window's own two, plus the two the embedded fieldstool MainWindow
    starts in its own __init__) so a torn-down window doesn't keep polling
    (and writing MockLogRecords /touching connections) in the background
    across the rest of the test session."""
    window = MainWindow(timeout_ms=10, verbose=False)
    yield window
    window._timer.stop()
    window._selection_timer.stop()
    window.fieldstool_dock.window._timer.stop()
    window.fieldstool_dock.window._selection_timer.stop()
    # Phase 4.3 — the embedded LogDock attaches its handler to the ROOT
    # logger; detach it so a session with several windows doesn't accumulate
    # handlers that keep every torn-down dock alive / keep logging forever.
    window.log_dock.remove_handler()
    if window._tray_icon is not None:
        window._tray_icon.hide()


@pytest.fixture
def log_dock(main_window):
    """LogDock attaches its handler to the ROOT logger and (since Phase
    4.3) detaches it via LogDock.remove_handler() when the dock is closed
    or destroyed — this fixture forces the root logger to DEBUG so
    handler-level filtering (INFO vs DEBUG) is actually what's being
    tested, matching what kicadstamp_gui.py's setup_logging() does for
    real (root logger at DEBUG, each handler filters independently) —
    see kicadstamp/logging_setup.py."""
    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.DEBUG)
    dock = LogDock(main_window, verbose=False)
    yield dock
    dock.remove_handler()
    root.setLevel(original_level)
