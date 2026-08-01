# gui/ui_utils.py
"""
Shared UI helpers for the kicadstamp GUI docks. The busy-indicator context
manager is the first one; the dock-level utilities (_show_message/
_set_combo_items/read-merge-write helpers) now live in gui/docks/_common.py
(see the gui/ cleanup roadmap, Phase 2). This file stays at package level
rather than under docks/ because gui/main_window.py may want it too, not
just the docks.
"""
from contextlib import contextmanager
from typing import Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget


@contextmanager
def busy(buttons: Iterable[QWidget] = ()):
    """Signals "work in progress" around a long, synchronous (UI-thread)
    operation — sets the wait cursor and disables the given buttons for the
    duration, restoring both afterwards.

    The operation itself still runs on the UI thread, so the GUI can't
    repaint mid-run — but the cursor + disabled buttons communicate 'busy'
    before and after, and — more importantly — disabling the trigger button
    prevents a second click from queueing a concurrent duplicate run (e.g.
    a double-click on Redraw starting two ApplyPipelines against the same
    pynng REQ socket).

    Buttons are restored to their PRE-existing enabled state (a button that
    was already disabled — e.g. Extract with nothing selected — stays
    disabled). Safe when no QApplication exists yet (unit-test edge): the
    cursor calls are skipped, and the context is a no-op apart from the
    buttons.
    """
    app = QApplication.instance()
    if app is not None:
        app.setOverrideCursor(Qt.CursorShape.WaitCursor)
    states = {button: button.isEnabled() for button in buttons}
    for button in buttons:
        button.setEnabled(False)
    try:
        yield
    finally:
        for button in buttons:
            button.setEnabled(states[button])
        if app is not None:
            app.restoreOverrideCursor()
