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
from typing import List, Optional

from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.explore import Board, Selected

logger = logging.getLogger(__name__)


class BoardConnection:
    def __init__(self, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        self.timeout_ms = timeout_ms
        self.board: Optional[Board] = None
        # Phase 5.2 — held exclusively by a background long op (Extract/
        # Redraw, see gui/worker.py): while True, MainWindow's polling timers
        # skip their ticks so this kipy REQ socket has exactly one in-flight
        # owner at a time (Extract runs on this shared socket directly).
        self.long_op_active = False
        # Full-board footprint snapshot (Board.select() with no filters),
        # rebuilt ONLY by _rebuild_snapshot() — i.e. on connect()/refresh()
        # (the ~2s poll / manual Refresh in gui/main_window.py._poll), never
        # on the 400ms selection-watch tick. Building it there was the main
        # perf bug of that tick (it re-ran board.select() over every
        # footprint 2-3x a second for no user-visible reason); consumers of
        # the tick build their `selected` lists by ref against this cache
        # instead. _snapshot_version lets those consumers tell "board data
        # changed" apart from "same data, new tick".
        self._snapshot: List[Selected] = []
        self._snapshot_version = 0

    @property
    def is_connected(self) -> bool:
        return self.board is not None

    @property
    def snapshot(self) -> List[Selected]:
        return self._snapshot

    @property
    def snapshot_version(self) -> int:
        """Incremented every time the cached snapshot is rebuilt — a cheap,
        stable identity for "the board data changed since my last look",
        usable as a guard key by tick-based consumers (see
        MainWindow._poll_board_selection)."""
        return self._snapshot_version

    def _rebuild_snapshot(self) -> None:
        self._snapshot = self.board.select()
        self._snapshot_version += 1

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
        try:
            # Board.connect() already called refresh(), so the snapshot is
            # immediately consistent with the live board.
            self._rebuild_snapshot()
        except Exception as e:
            logger.warning("Snapshot after connect failed, dropping connection: %s", e)
            self.board = None
            return str(e)
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
            self._rebuild_snapshot()
            return None
        except Exception as e:
            logger.warning("Refresh failed, dropping connection: %s", e)
            self.board = None
            return str(e)
