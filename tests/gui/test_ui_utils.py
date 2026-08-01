# tests/gui/test_ui_utils.py
"""Tests for gui/ui_utils.py — the shared busy() context manager used by
PlacerDock/ExtractDock to signal "work in progress" around long synchronous
operations (wait cursor + trigger buttons disabled)."""
from PyQt6.QtWidgets import QPushButton

from gui.ui_utils import busy


def test_busy_disables_and_restores_buttons(qapp):
    button = QPushButton()
    assert button.isEnabled()
    with busy((button,)):
        assert not button.isEnabled()
    assert button.isEnabled()


def test_busy_restores_prior_disabled_state(qapp):
    """A button that was already disabled before busy() must stay disabled
    afterwards — busy() must not flip it back on (e.g. Extract's button when
    nothing is selected)."""
    button = QPushButton()
    button.setEnabled(False)
    with busy((button,)):
        assert not button.isEnabled()
    assert not button.isEnabled()


def test_busy_restores_state_on_exception(qapp):
    """Even if the wrapped operation raises, buttons/cursor must be restored
    (the finally branch), so a failed Redraw/Extract doesn't leave the UI
    locked.""" 
    button = QPushButton()
    try:
        with busy((button,)):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert button.isEnabled()
