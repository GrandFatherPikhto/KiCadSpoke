# gui/yaml_io.py
"""
Small shared YAML read helpers for the docks that browse an already-written
config file's contents (ExtractDock's "Existing cells:"/"Existing profiles:"
lists, PlacerDock's Cell list). Split out once PlacerDock needed the exact
same "read this file, give me its top-level (or nested-section) keys"
logic ExtractDock already had — see gui/docks/extract.py's _load_data()/
_existing_keys() history.
"""
import json
import logging
from pathlib import Path
from typing import Optional, Set

import yaml

logger = logging.getLogger(__name__)


def load_data(path: Optional[Path]) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return (json.load(f) if path.suffix.lower() == ".json" else yaml.safe_load(f)) or {}
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return {}


def existing_keys(path: Optional[Path], section: Optional[str] = None) -> Set[str]:
    data = load_data(path)
    if section is not None:
        data = data.get(section) or {}
    return set(data.keys())
