# gui/docks/log_panel.py
"""
LogDock — a read-only, copyable/searchable log panel docked at the bottom
of the window, fed by a logging.Handler attached to the ROOT logger (not
just kicadstamp.*) — every existing logger.info/warning/exception() call
anywhere in the backend (kicadstamp/, kicadstamp/placement/, etc.) shows
up here for free, with zero changes to those call sites. Closes a real
gap: things like extract_template_from_selection()'s "N nets from
--net-template on pads" warning previously only ever reached the console
(or nowhere, if the GUI wasn't launched from one), invisible in the GUI
itself.

Requested live 2026-08-01: dock message_labels are fine for a one-line
status next to the button that produced it, but the user wanted a plain
scrollable panel for everything else — "редактировать нельзя, копировать
и искать -- можно" (can't edit, but can copy and search). A read-only
QPlainTextEdit already behaves exactly like that (selection/copy work,
typing doesn't); the Find row adds the "искать" half explicitly, since
QPlainTextEdit has no built-in find UI of its own.

setup_logging() (kicadstamp/logging_setup.py) already sets the ROOT
logger's level to DEBUG unconditionally and relies on each individual
HANDLER's own level to filter what actually gets shown (see its console
StreamHandler) — the Verbose checkbox here follows the same pattern:
toggles THIS handler's level between INFO/DEBUG, never the root logger's,
so it can't accidentally silence or unmute the console/file handlers
kicadstamp_gui.py already set up.
"""
import html
import logging

from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import (QCheckBox, QDockWidget, QHBoxLayout, QLineEdit,
                              QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from kicadstamp.i18n import _

_LEVEL_COLOR = {
    logging.DEBUG: "#888888",
    logging.WARNING: "#a86a00",
    logging.ERROR: "#aa0000",
    logging.CRITICAL: "#aa0000",
}


class _QtLogHandler(logging.Handler):
    def __init__(self, dock: "LogDock"):
        super().__init__()
        self._dock = dock

    def emit(self, record: logging.LogRecord) -> None:
        # Same thread as every kipy call in this app (see main_window.py's
        # module docstring on why there's no QThread here) — appending
        # straight to the widget from within emit() is safe, no queuing
        # needed.
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self._dock.append_line(message, record.levelno)


class LogDock(QDockWidget):
    def __init__(self, main_window, verbose: bool = False):
        super().__init__(_("Log"), main_window)
        self._main_window = main_window

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        top_row = QHBoxLayout()
        self.verbose_checkbox = QCheckBox(_("Verbose"))
        self.verbose_checkbox.toggled.connect(self._on_verbose_toggled)
        top_row.addWidget(self.verbose_checkbox)
        top_row.addStretch(1)
        clear_button = QPushButton(_("Clear"))
        clear_button.clicked.connect(lambda: self.text.clear())
        top_row.addWidget(clear_button)
        layout.addLayout(top_row)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(10000)  # cap growth over a long-running session
        layout.addWidget(self.text, 1)

        find_row = QHBoxLayout()
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText(_("Find..."))
        self.find_edit.returnPressed.connect(lambda: self._find(backward=False))
        find_row.addWidget(self.find_edit, 1)
        find_prev_button = QPushButton(_("Prev"))
        find_prev_button.clicked.connect(lambda: self._find(backward=True))
        find_row.addWidget(find_prev_button)
        find_next_button = QPushButton(_("Next"))
        find_next_button.clicked.connect(lambda: self._find(backward=False))
        find_row.addWidget(find_next_button)
        layout.addLayout(find_row)

        self.setWidget(container)

        self._handler = _QtLogHandler(self)
        self._handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
        self._handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        logging.getLogger().addHandler(self._handler)
        self.verbose_checkbox.setChecked(verbose)

    def _on_verbose_toggled(self, checked: bool) -> None:
        self._handler.setLevel(logging.DEBUG if checked else logging.INFO)

    def _find(self, backward: bool) -> None:
        query = self.find_edit.text()
        if not query:
            return
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        found = self.text.find(query, flags)
        if not found:
            # Wrap around: retry once from the start/end of the document.
            cursor = self.text.textCursor()
            cursor.movePosition(cursor.MoveOperation.End if backward else cursor.MoveOperation.Start)
            self.text.setTextCursor(cursor)
            self.text.find(query, flags)

    def append_line(self, message: str, levelno: int) -> None:
        color = _LEVEL_COLOR.get(levelno)
        if color:
            self.text.appendHtml(f'<span style="color:{color}">{html.escape(message)}</span>')
        else:
            self.text.appendPlainText(message)
