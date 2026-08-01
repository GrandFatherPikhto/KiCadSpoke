# tests/fieldstool_gui/conftest.py
"""
Shared fixtures for fieldstool/gui/ tests — mirrors tests/gui/conftest.py
exactly (same reasoning: offscreen, no live KiCad, isolated settings
file so a test run never touches the developer's real
fieldstool_gui_state.json).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtWidgets import QApplication

from fieldstool.gui import settings


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "fieldstool_gui_state.json")


class _FakeConnection:
    board = None
    is_connected = False


@pytest.fixture
def main_window(qapp):
    """A real MainWindow, constructed for real (not a QMainWindow stub —
    unlike tests/gui/conftest.py's main_window fixture, fieldstool's docks
    are simple enough to build the whole window rather than faking a
    parent), with timeout_ms kept tiny since nothing here ever actually
    connects."""
    from fieldstool.gui.main_window import MainWindow
    window = MainWindow(timeout_ms=50)
    yield window
    window._timer.stop()
    window._selection_timer.stop()
