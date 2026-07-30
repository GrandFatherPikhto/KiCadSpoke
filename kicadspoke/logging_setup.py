# kicadspoke/logging_setup.py
"""
Logging setup for the KiCadSpoke CLI.

Extracted from kicadspoke_cli.py so board scripts (via author.py) and
any other entry point can configure logging without importing the full CLI.
"""

import logging
import sys
from pathlib import Path


class _ColorFormatter(logging.Formatter):
    """Wraps ERROR/CRITICAL lines in red, WARNING in yellow — ANSI escape
    codes, only when the console stream is a real terminal (use_color), so
    redirected/piped output never gets raw escape bytes.  format_fatal_error()
    already marks each problem with '✗' — this makes the whole FATAL ERROR
    block visually impossible to miss instead of blending into a wall of
    INFO lines (found needed live: ambiguity errors from a board script were
    easy to scroll past in a long --apply log)."""
    _RED = "\033[91m"
    _YELLOW = "\033[93m"
    _RESET = "\033[0m"

    def __init__(self, fmt: str, use_color: bool):
        super().__init__(fmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not self._use_color:
            return message
        if record.levelno >= logging.ERROR:
            return f"{self._RED}{message}{self._RESET}"
        if record.levelno == logging.WARNING:
            return f"{self._YELLOW}{message}{self._RESET}"
        return message


def setup_logging(verbose: bool = False, log_file: str = None) -> None:
    """Configure logging: level and output to console and/or file."""
    level = logging.DEBUG if verbose else logging.INFO
    handlers = []
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    use_color = hasattr(console.stream, "isatty") and console.stream.isatty()
    console.setFormatter(_ColorFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s", use_color=use_color))
    handlers.append(console)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        handlers.append(file_handler)

    logging.basicConfig(level=logging.DEBUG, handlers=handlers)
