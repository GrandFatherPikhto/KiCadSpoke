# gui/settings.py
"""
Small persistent JSON settings for the GUI — a flat dict in one file for
anything the GUI needs to remember between launches (which board directory was
last active, dock layout, etc.) rather than inventing a new file/format per
setting.

Phase 4.1 of the gui-optimization roadmap: the scattered `load() → mutate →
save()` pattern is replaced by a single [`Settings`](gui/settings.py) object
with pointwise `get(key, default)` / `set(key, value)` and an atomic write.
The on-disk format is unchanged (plain JSON, `gui_state.json`), so an existing
settings file keeps working untouched.

*.json is already blanket-gitignored (see .gitignore) — this is exactly the
kind of local/generated, not-source-of-truth data that pattern already covers
(same bucket as test_boards/, boards/*/generated/); the atomic-write temp file
(*.json.tmp) is gitignored too.
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).resolve().parent / "gui_state.json"


def _read(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read %s, ignoring: %s", path, e)
        return {}


def _write(path: Path, data: Dict[str, Any]) -> None:
    """Atomic write: dump to a temp file next to the target, then
    os.replace() — a reader can never observe a half-written gui_state.json
    (e.g. if the process dies mid-write). A leftover temp file from a crash
    is harmless (overwritten on the next write) and gitignored."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Failed to write %s: %s", path, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


class Settings:
    """Pointwise access to the one GUI state file (gui_state.json).

    Every get()/set() re-reads the file from disk, and set() re-merges its
    single key into the latest state before the atomic write — so any number
    of components (docks, MainWindow) can share the file without ever
    clobbering each other's keys. This is the same merge semantics the old
    load() → mutate → save() callers hand-rolled, but in one place.

    A path of None resolves gui.settings.SETTINGS_PATH at call time (not
    construction time) so tests can monkeypatch it (tests/gui/conftest.py)
    after this module has been imported.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = path

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else SETTINGS_PATH

    def get(self, key: str, default: Any = None) -> Any:
        return _read(self.path).get(key, default)

    def set(self, key: str, value: Any) -> None:
        data = _read(self.path)
        data[key] = value
        _write(self.path, data)


# Module-level singleton — the whole GUI shares one file, so docks use
# `settings.state.get(...)` / `settings.state.set(...)` instead of each
# holding its own Settings instance.
state = Settings()


# Backward-compatible whole-dict API, still used by tests to seed/assert
# state (and by callers that legitimately want the full dict at once).
def load() -> Dict[str, Any]:
    return _read(SETTINGS_PATH)


def save(data: Dict[str, Any]) -> None:
    _write(SETTINGS_PATH, data)
