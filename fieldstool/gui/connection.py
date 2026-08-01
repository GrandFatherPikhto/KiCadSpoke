# fieldstool/gui/connection.py
"""
BoardConnection — thin, READ-ONLY lifecycle wrapper around
kicadstamp.explore.Board, mirroring gui/connection.py's shape (connect/
refresh, error-string-not-exception, retryable from a QTimer) but
deliberately narrower: only ever used to watch the LIVE selection while
staging pending changes (see fieldstool/gui/pending.py) — KiCad is
expected to stay open for this half of the workflow. Never calls a write
method. Does NOT call check_write_crash_risk() (KiCad bug #24966,
kicadstamp/kicad/adapter.py) — that check only matters before the FIRST
MUTATING IPC write of a session, and this connection never writes at all.
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
        """Returns None on success, or an error message on failure — never
        raises, so a QTimer tick doesn't need a try/except at every call
        site (same contract as gui/connection.py's BoardConnection)."""
        try:
            board = Board.connect(timeout_ms=self.timeout_ms)
        except Exception as e:
            logger.debug("Connect failed: %s", e)
            return str(e)
        self.board = board
        logger.info("fieldstool: connected to KiCad")
        return None

    def refresh(self) -> Optional[str]:
        if self.board is None:
            return "not connected"
        try:
            self.board.refresh()
            return None
        except Exception as e:
            logger.warning("Refresh failed, dropping connection: %s", e)
            self.board = None
            return str(e)
