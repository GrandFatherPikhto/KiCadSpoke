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
    dock = LogDock(main_window, verbose=True)
    try:
        assert dock.verbose_checkbox.isChecked()
    finally:
        dock.remove_handler()
        root.setLevel(original_level)


def test_remove_handler_detaches_idempotently_and_stops_receiving(main_window):
    """Phase 4.3 — remove_handler() is the single teardown path for the
    root-logger handler: it detaches the handler, is safe to call twice
    (closeEvent + destroyed + fixture teardown can all fire), and a
    detached handler no longer feeds the panel."""
    from gui.docks.log_panel import LogDock

    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.DEBUG)
    dock = LogDock(main_window, verbose=False)
    try:
        assert dock._handler in root.handlers
        dock.remove_handler()
        assert dock._handler not in root.handlers
        # idempotent — a second call (e.g. from the destroyed signal after
        # closeEvent already ran) must be a no-op
        dock.remove_handler()
        assert dock._handler not in root.handlers
        # detached handler no longer feeds the panel
        logging.getLogger("kicadstamp.gui_test.removed").info("after removal")
        assert "after removal" not in dock.text.toPlainText()
    finally:
        dock.remove_handler()
        root.setLevel(original_level)


def test_close_removes_handler_and_reshow_readds(real_main_window):
    """Phase 4.3 — closing the dock detaches its root-logger handler (so a
    window torn down while the dock is closed can't leak it), and showing
    the dock again re-attaches it, so closing/reopening from the View menu
    keeps the panel live."""
    root = logging.getLogger()
    dock = real_main_window.log_dock
    assert dock._handler in root.handlers

    real_main_window.show()
    dock.close()
    assert dock._handler not in root.handlers

    dock.show()
    assert dock._handler in root.handlers
    # teardown of real_main_window also calls remove_handler() — idempotent
    dock.remove_handler()
    assert dock._handler not in root.handlers
