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

from gui import settings
from gui.docks.log_panel import LogDock


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
    no test can forget it and accidentally pollute real GUI state."""
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "gui_state.json")


class _FakeConnection:
    board = None


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
def log_dock(main_window):
    """LogDock attaches its handler to the ROOT logger and never removes
    it on its own (fine for the one instance a real GUI process creates,
    not fine across many test functions in one session) — this fixture
    tears that down, and temporarily forces the root logger to DEBUG so
    handler-level filtering (INFO vs DEBUG) is actually what's being
    tested, matching what kicadstamp_gui.py's setup_logging() does for
    real (root logger at DEBUG, each handler filters independently) —
    see kicadstamp/logging_setup.py."""
    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.DEBUG)
    dock = LogDock(main_window, verbose=False)
    yield dock
    root.removeHandler(dock._handler)
    root.setLevel(original_level)
