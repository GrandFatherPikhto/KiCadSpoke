# tests/gui/test_log_panel.py
import logging


def test_level_filtering_and_verbose_toggle(log_dock):
    test_logger = logging.getLogger("kicadstamp.gui_test.level_filtering")

    test_logger.debug("hidden debug line")
    test_logger.info("visible info line")
    test_logger.warning("visible warning line")

    text = log_dock.text.toPlainText()
    assert "hidden debug line" not in text
    assert "visible info line" in text
    assert "visible warning line" in text

    log_dock.verbose_checkbox.setChecked(True)
    test_logger.debug("now visible debug line")
    assert "now visible debug line" in log_dock.text.toPlainText()

    log_dock.verbose_checkbox.setChecked(False)
    test_logger.debug("hidden again")
    assert "hidden again" not in log_dock.text.toPlainText()


def test_clear_button_empties_the_panel(log_dock):
    test_logger = logging.getLogger("kicadstamp.gui_test.clear")
    test_logger.info("something")
    assert "something" in log_dock.text.toPlainText()

    log_dock.text.clear()
    assert log_dock.text.toPlainText() == ""


def test_find_selects_matching_text(log_dock):
    log_dock.text.appendPlainText("a needle in a haystack")
    log_dock.find_edit.setText("needle")
    log_dock._find(backward=False)
    assert log_dock.text.textCursor().hasSelection()


def test_verbose_checkbox_seeded_from_constructor_flag(main_window):
    import logging as logging_module

    from gui.docks.log_panel import LogDock

    root = logging_module.getLogger()
    original_level = root.level
    root.setLevel(logging_module.DEBUG)
    try:
        dock = LogDock(main_window, verbose=True)
        try:
            assert dock.verbose_checkbox.isChecked()
        finally:
            root.removeHandler(dock._handler)
    finally:
        root.setLevel(original_level)
