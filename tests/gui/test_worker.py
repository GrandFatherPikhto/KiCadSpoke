# tests/gui/test_worker.py
"""
Phase 5.2 — worker-thread long ops (gui/worker.py).

These run a REAL QThread (LongOpController) and pin down the serialization
contract that keeps the kipy REQ socket single-in-flight while a long op is
off the UI thread:

  * connection.long_op_active is set BEFORE the worker thread starts and
    cleared AFTER it finishes (on the UI thread, via queued signals), so no
    polling tick can ever race the op's first socket request;
  * guard widgets are disabled for the duration and restored afterwards;
  * results/errors come back through signals on the UI thread;
  * while the flag is set, the main window's polling ticks skip their socket
    work (_poll / _poll_board_selection), so the op is the socket's only
    active owner for its whole lifetime.

The QThread tests deliberately avoid touching controller._thread after the
event loop has been pumped (its finished->deleteLater chain may already have
deleted the C++ object); the thread reference is captured right after
start() (before any pump) and only used for a final wait(), which is safe
because the completion handler has already run (posting quit()) by then.
"""
import threading
import time
from types import SimpleNamespace

from gui.worker import LongOpController, start_long_op


class _Button:
    """Stand-in for the two QWidget methods LongOpController touches —
    the worker never needs a real widget."""

    def __init__(self):
        self._enabled = True

    def isEnabled(self):
        return self._enabled

    def setEnabled(self, enabled):
        self._enabled = enabled


def _pump(qapp, until, timeout=5.0):
    """Pump the Qt event loop until `until()` is truthy or the deadline
    passes. Completion signals from the worker thread are queued to the UI
    thread, so they only fire while this pump is running."""
    deadline = time.monotonic() + timeout
    while not until():
        if time.monotonic() > deadline:
            raise TimeoutError("timed out waiting for worker signal")
        qapp.processEvents()
        time.sleep(0.005)


def test_controller_holds_and_releases_socket_around_op(qapp):
    """The flag + guard-widget disable happen BEFORE the worker thread
    starts (so no polling tick can race the op's first request), and both
    are released only after the op completes back on the UI thread."""
    connection = SimpleNamespace(long_op_active=False)
    button = _Button()
    entered = threading.Event()
    release = threading.Event()

    def slow_op():
        # Runs on the worker thread. start() set the flag + disabled the
        # widget synchronously on the UI thread before the thread started,
        # so both are already true here — if not, the assertion raises and
        # entered is never set, failing the test below.
        assert connection.long_op_active is True
        assert not button.isEnabled()
        entered.set()
        assert release.wait(5.0)  # hold the op open so the test can observe
        return "done"

    results = []
    controller = LongOpController(connection, [button])
    controller.finished.connect(results.append)
    controller.start(slow_op)
    thread = controller._thread  # capture before any pump (see module docstring)

    assert entered.wait(5.0), "worker never started"
    # Mid-flight: the op holds the socket exclusively and the guard widget
    # stays disabled for the whole duration.
    assert connection.long_op_active is True
    assert not button.isEnabled()

    release.set()
    _pump(qapp, lambda: results)
    assert thread.wait(2000), "worker thread did not finish"

    assert results == ["done"]
    assert connection.long_op_active is False
    assert button.isEnabled()


def test_controller_failed_path_releases_socket(qapp):
    """Even when the worker fn raises, the socket is released and the guard
    widgets restored — a worker bug can never leave the connection stuck
    'long op active'."""
    connection = SimpleNamespace(long_op_active=False)
    button = _Button()
    errors = []

    def boom():
        raise ValueError("kaboom")

    controller = LongOpController(connection, [button])
    controller.failed.connect(errors.append)
    controller.start(boom)
    thread = controller._thread

    _pump(qapp, lambda: errors)
    assert thread.wait(2000), "worker thread did not finish"

    assert errors == ["kaboom"]
    assert connection.long_op_active is False
    assert button.isEnabled()


def test_start_long_op_wires_signals_and_returns_controller(qapp):
    """start_long_op() is the factory the docks call: it must return the
    controller (the docks keep it as self._active_op to prevent GC of a
    parent-less QThread), wire finished->on_success / failed->on_error, and
    run fn(*args) off the UI thread."""
    connection = SimpleNamespace(long_op_active=False)
    results = []
    errors = []

    controller = start_long_op(
        connection, [], lambda x: x * 2, results.append, errors.append, 21)
    assert isinstance(controller, LongOpController)
    thread = controller._thread

    _pump(qapp, lambda: results)
    assert thread.wait(2000), "worker thread did not finish"

    assert results == [42]
    assert errors == []
    assert connection.long_op_active is False


def test_poll_suspends_during_long_op(real_main_window, monkeypatch, qapp):
    """While long_op_active is set, _poll() must not touch the socket at
    all — not even a manual (button-click) refresh, which would interleave
    a connect()/refresh() into the op's in-flight REQ. _poll() itself now
    dispatches through start_long_op (2026-08-03 fix), so the second call's
    effect only lands after pumping the event loop — see conftest._pump's
    docstring for why the guard is `not long_op_active`, not "connect_calls
    is non-empty"."""
    window = real_main_window
    window._timer.stop()  # deterministic: no auto-tick can fire mid-test
    window._selection_timer.stop()
    # No live KiCad here regardless (MainWindow no longer connects at
    # construction time at all, see gui/main_window.py's __init__) — forced
    # anyway so this test never depends on that.
    window.connection.board = None

    connect_calls = []
    monkeypatch.setattr(window.connection, "connect",
                        lambda: connect_calls.append(1) or "fake error")

    window.connection.long_op_active = True
    window._poll(manual=True)
    assert connect_calls == []

    window.connection.long_op_active = False
    window._poll(manual=True)
    _pump(qapp, lambda: not window.connection.long_op_active)
    assert connect_calls == [1]


def test_selection_tick_suspends_during_long_op(real_main_window, monkeypatch, qapp):
    """While long_op_active is set, the fast selection-watch tick must not
    call get_selected_items() on the shared socket either. Same dispatch/pump
    reasoning as test_poll_suspends_during_long_op above."""
    window = real_main_window
    window._timer.stop()
    window._selection_timer.stop()

    get_selected_calls = []
    window.connection.board = SimpleNamespace(
        adapter=SimpleNamespace(
            get_selected_items=lambda: get_selected_calls.append(1) or []))
    # is_connected is a property (board is not None), so the tick would
    # normally proceed — only the long-op flag holds it back.

    window.connection.long_op_active = True
    window._poll_board_selection()
    assert get_selected_calls == []

    window.connection.long_op_active = False
    window._poll_board_selection()
    _pump(qapp, lambda: not window.connection.long_op_active)
    assert get_selected_calls == [1]
