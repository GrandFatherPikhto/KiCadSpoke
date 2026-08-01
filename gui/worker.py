# gui/worker.py
"""
Long-operation worker — moves blocking, IPC-heavy work (Extract / Redraw)
off the UI thread while preserving the kipy REQ socket's single-in-flight
rule.

Why this exists (Phase 5.2): kipy's connection is a pynng.Req0 socket —
exactly ONE request may be in flight per socket at a time. Two concurrent
owners (a background op + the GUI's polling timers) would interleave
requests mid-flight and corrupt the stream. Today those ops run on the UI
thread, which blocks the GUI's QTimers entirely (so there is implicitly only
one active socket) but freezes the window for the duration. Moving the op to
a worker thread would let the polling timers keep firing concurrently, so a
worker alone is not enough — the shared socket must be serialized.

Serialization model:
  * The shared BoardConnection carries a plain `long_op_active` flag.
  * LongOpController.start() sets it on the UI thread BEFORE the worker
    thread starts, and _release() clears it on the UI thread AFTER the op
    finishes (completion handlers run back on the UI thread via queued
    signal connections — the worker lives in a different thread).
  * While the flag is set, MainWindow._poll / _poll_board_selection and the
    embedded fieldstool's _push_selection_to_board skip their ticks, so the
    socket has exactly one active owner for the whole op. Extract uses the
    shared socket directly (board.adapter), making suspension mandatory;
    Redraw's ApplyPipeline opens its OWN kipy socket, so for it the flag is
    a coordination token that reproduces today's blocked-UI behaviour (no
    second socket while a write is underway).

QWidget safety: QWidgets may only be touched on the UI thread. Each op is
therefore split by the caller into: collect-inputs (UI thread — validation
+ widget reads), run (worker — pure IPC + file IO, NO widget access),
finish (UI thread — widget writes). This module only orchestrates the
thread boundary; the split lives in the docks.
"""
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)


class _LongOpWorker(QObject):
    """Runs fn(*args) on the worker thread and reports the outcome via
    signals. succeeded() carries the return value; failed() carries a
    human-readable message. Every exception is caught and routed to failed()
    so a worker bug can never silently kill the thread or leave the socket
    held."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[..., Any], args: tuple, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._args = args

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self._fn(*self._args)
        except Exception as e:
            logger.exception("Long operation failed")
            self.failed.emit(str(e))
            return
        self.succeeded.emit(result)


class LongOpController(QObject):
    """Owns the QThread + worker for one long operation. Must be created on
    the UI thread. start() acquires the shared socket (connection.
    long_op_active = True) and disables the guard widgets BEFORE the thread
    starts; the completion handlers run back on the UI thread (queued
    connections, since the worker lives in a different thread) and release
    the socket exactly once."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, connection: Any, widgets: Iterable[Any], parent=None):
        super().__init__(parent)
        self._connection = connection
        self._widgets: List[Any] = list(widgets)
        self._thread: Optional[QThread] = None
        self._worker: Optional[_LongOpWorker] = None
        self._released = False
        self._prior_enabled: Dict[Any, bool] = {}

    def start(self, fn: Callable[..., Any], *args) -> None:
        self._acquire()
        self._thread = QThread(self)
        self._worker = _LongOpWorker(fn, args)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._on_worker_succeeded)
        self._worker.failed.connect(self._on_worker_failed)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _acquire(self) -> None:
        # Set on the UI thread BEFORE the worker starts so no polling tick
        # can race the op's first socket request.
        if self._connection is not None:
            self._connection.long_op_active = True
        for w in self._widgets:
            self._prior_enabled[w] = w.isEnabled()
            w.setEnabled(False)

    def _release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._connection is not None:
            self._connection.long_op_active = False
        for w, enabled in self._prior_enabled.items():
            w.setEnabled(enabled)
        if self._thread is not None:
            self._thread.quit()

    @pyqtSlot(object)
    def _on_worker_succeeded(self, result: Any) -> None:
        self._release()
        self.finished.emit(result)

    @pyqtSlot(str)
    def _on_worker_failed(self, message: str) -> None:
        self._release()
        self.failed.emit(message)


def start_long_op(connection, widgets, fn, on_success, on_error, *args):
    """Convenience factory: builds a LongOpController, wires its finished/
    failed signals to on_success/on_error (both called on the UI thread),
    starts the op, and returns the controller so the caller can keep a
    reference (preventing GC of a parent-less QThread) and inspect state if
    needed."""
    controller = LongOpController(connection, widgets)
    controller.finished.connect(on_success)
    controller.failed.connect(on_error)
    controller.start(fn, *args)
    return controller
