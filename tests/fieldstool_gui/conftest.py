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
    """Mirrors the shape of gui/connection.py's BoardConnection (the only
    connection MainWindow ever receives — always injected by the embedding
    main GUI). is_connected is a property, not a static attribute, so tests
    that set .board after construction see it flip automatically, matching
    the real class."""

    def __init__(self):
        self.board = None
        self.long_op_active = False

    @property
    def is_connected(self) -> bool:
        return self.board is not None


@pytest.fixture
def main_window(qapp):
    """A real MainWindow, constructed for real (not a QMainWindow stub —
    unlike tests/gui/conftest.py's main_window fixture, fieldstool's docks
    are simple enough to build the whole window rather than faking a
    parent), with a fake connection since MainWindow never creates its own
    (always injected by the embedding main GUI, see
    fieldstool/gui/main_window.py)."""
    from fieldstool.gui.main_window import MainWindow
    window = MainWindow(connection=_FakeConnection())
    yield window
