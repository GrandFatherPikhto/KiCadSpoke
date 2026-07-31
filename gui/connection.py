# gui/connection.py
"""
BoardConnection — thin lifecycle wrapper around kicadstamp.explore.Board for
the GUI. Unlike the CLI (one run, connect-or-die), this app is meant to sit
open persistently alongside KiCad — KiCad may not be running yet when the
GUI starts, or may close/crash while the GUI stays open — so connecting is a
deliberate, retryable action polled from a QTimer, not something assumed to
succeed once at startup.
"""
import logging
from typing import Optional

from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.explore import Board

logger = logging.getLogger(__name__)


class BoardConnection:
    def __init__(self, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        self.timeout_ms = timeout_ms
        self.board: Optional[Board] = None

    @property
    def is_connected(self) -> bool:
        return self.board is not None

    def connect(self) -> Optional[str]:
        """Attempts a fresh connection. Returns None on success, or an error
        message on failure — never raises, so a QTimer tick doesn't need a
        try/except at every call site."""
        try:
            board = Board.connect(timeout_ms=self.timeout_ms)
        except Exception as e:
            logger.debug("Connect failed: %s", e)
            return str(e)
        # See KiCadBoardAdapter.check_write_crash_risk()'s docstring (issue
        # #24966) — cheap to call once up front, before any dock has a
        # chance to call select_items()/set_field_value() for the first time.
        board.adapter.check_write_crash_risk()
        self.board = board
        logger.info("Connected to KiCad")
        return None

    def refresh(self) -> Optional[str]:
        """Re-fetches the footprint snapshot on an already-connected board.
        On failure (KiCad closed/crashed since connect()), drops the
        connection so the next tick retries connect() from scratch instead
        of repeating the same stale error forever."""
        if self.board is None:
            return "not connected"
        try:
            self.board.refresh()
            return None
        except Exception as e:
            logger.warning("Refresh failed, dropping connection: %s", e)
            self.board = None
            return str(e)
