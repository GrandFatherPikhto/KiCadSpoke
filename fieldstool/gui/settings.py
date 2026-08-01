# fieldstool/gui/settings.py
"""
Small persistent JSON settings for fieldstool's GUI — mirrors
gui/settings.py's shape exactly (plain JSON, not QSettings — same
"stay human-readable/inspectable" bias). Separate file from
gui/gui_state.json — these are two independent processes/apps.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).resolve().parent / "fieldstool_gui_state.json"


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
