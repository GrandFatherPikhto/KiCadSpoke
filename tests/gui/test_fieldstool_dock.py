# tests/gui/test_fieldstool_dock.py
"""
FieldsToolDock wraps a real fieldstool MainWindow and is tabified first in
the right-hand dock group (replacing the retired BulkFieldEditorDock slot).
"""
from fieldstool.gui.main_window import MainWindow as FieldsToolMainWindow


def test_wraps_a_real_fieldstool_main_window(real_main_window):
    assert isinstance(real_main_window.fieldstool_dock.window, FieldsToolMainWindow)
    assert real_main_window.fieldstool_dock.widget() is real_main_window.fieldstool_dock.window


def test_fieldstool_is_first_right_hand_tab(real_main_window):
    tabbed_with_fieldstool = real_main_window.tabifiedDockWidgets(real_main_window.fieldstool_dock)
    assert real_main_window.file_picker_dock in tabbed_with_fieldstool
    assert real_main_window.extract_dock in tabbed_with_fieldstool
    assert real_main_window.placer_dock in tabbed_with_fieldstool


def test_open_fieldstool_shows_and_raises_the_dock(real_main_window):
    real_main_window.fieldstool_dock.setVisible(False)
    real_main_window.open_fieldstool()
    assert real_main_window.fieldstool_dock.isVisible()
    assert real_main_window.isVisible()
