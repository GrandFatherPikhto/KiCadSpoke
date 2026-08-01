# tests/gui/test_single_instance.py
"""
SingleInstanceGuard: first launch acquires and listens, a second launch
attempt with the same name is refused and pings the first (which raises
itself via activation_requested), and a stale server name left behind by
release() doesn't permanently block future launches.

Each test uses a uuid-based server name so tests never collide with each
other, with a real running kicadstamp_gui.py instance, or across parallel
test runs.
"""
import time
import uuid

from gui.single_instance import SingleInstanceGuard


def _unique_name() -> str:
    return f"kicadstamp-test-{uuid.uuid4().hex}"


def _wait_until(predicate, qapp, timeout_s: float = 2.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        qapp.processEvents()
    return False


def test_first_instance_acquires(qapp):
    guard = SingleInstanceGuard(_unique_name())
    assert guard.try_acquire() is True
    guard.release()


def test_second_instance_is_refused_and_pings_the_first(qapp):
    name = _unique_name()
    first = SingleInstanceGuard(name)
    assert first.try_acquire() is True

    activated = []
    first.activation_requested.connect(lambda: activated.append(True))

    second = SingleInstanceGuard(name)
    assert second.try_acquire() is False

    assert _wait_until(lambda: activated, qapp)

    first.release()


def test_release_frees_the_name_for_reacquisition(qapp):
    name = _unique_name()
    first = SingleInstanceGuard(name)
    assert first.try_acquire() is True
    first.release()

    second = SingleInstanceGuard(name)
    assert second.try_acquire() is True
    second.release()
