# gui/settings.py
"""
Small persistent JSON settings for the GUI — load()/save() a plain dict, one
flat file for anything the GUI needs to remember between launches (which
board directory was last active, dock layout, etc.) rather than inventing a
new file/format per setting.

*.json is already blanket-gitignored (see .gitignore) — this is exactly the
kind of local/generated, not-source-of-truth data that pattern already
covers (same bucket as test_boards/, boards/*/generated/), so this needs no
new gitignore entry.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).resolve().parent / "gui_state.json"


def load() -> Dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read %s, ignoring: %s", SETTINGS_PATH, e)
        return {}


def save(data: Dict[str, Any]) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except OSError as e:
        logger.warning("Failed to write %s: %s", SETTINGS_PATH, e)
