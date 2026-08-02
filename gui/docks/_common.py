# gui/docks/_common.py
"""Shared utilities for the kicadstamp GUI docks — the deduplicated
versions of helpers that ExtractDock / PlacerDock / FilePickerDock each
used to carry their own private copy of (Phase 2 of the gui/ cleanup
roadmap, see techdocs/handoff/).

Two groups live here:

* read-merge-write config helpers (merge_write / add_list_entry /
  upsert_clone_placement) — pure file operations shared by the docks'
  write paths. They read the existing YAML/JSON content, merge just the
  new data into it (never touching other top-level keys the file owns),
  and write back in the same format. The read deliberately does NOT
  swallow exceptions (unlike gui/yaml_io.load_data, which is for
  read-only browsing) — these helpers are on the docks' WRITE path,
  where a broken file must surface as an OSError the caller turns into
  an on-screen error message.

* Qt widget helpers (set_combo_items / configure_searchable /
  show_message / display_path) plus the message-label style constants —
  one definition instead of each dock declaring its own copy.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QCompleter, QLabel

from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

# Project root — the value FilePickerDock's file-tree is rooted at (it
# used to derive its own copy: gui/docks/file_picker.py). Single
# definition here so display_path() and the dock that owns the tree
# share one source of truth.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Message-label styles — one definition shared by every dock's status
# label, instead of each dock declaring the same three CSS color strings.
ERROR_STYLE = "color: #a00;"
WARN_STYLE = "color: #a60;"
SUCCESS_STYLE = "color: #070;"


def _read_data(path: Path) -> dict:
    """Read an existing config file's YAML/JSON content (or {} when it
    doesn't exist yet). Raises on read/parse errors instead of returning
    {} — the merge-write helpers are on the docks' write path, where a
    broken file must surface to the user, not be silently treated as
    empty (unlike gui/yaml_io.load_data)."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return (json.load(f) if path.suffix.lower() == ".json" else yaml.safe_load(f)) or {}


def _write_data(path: Path, data: dict) -> None:
    """Write merged content back in the same format (YAML/JSON by file
    extension) it was read in."""
    with open(path, "w", encoding="utf-8") as f:
        if path.suffix.lower() == ".json":
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=False)
        else:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def merge_write(path: Path, new_data: dict, section: Optional[str] = None) -> bool:
    """Same read-merge-write shape as kicadstamp_cli.py's cmd_extract:
    existing content in the target file is kept, only what's in new_data
    is added/replaced — a target file is routinely home to several
    cells/profiles accumulated over time, not exclusively owned by this
    one write.

    section=None: new_data is merged directly at the file's top level.
    section='cells'/'extract_profiles'/etc.: new_data is
    {section: {key: {...}}} — only that one nested dict gets merged,
    every OTHER top-level key already in the file (clone_placements:,
    include:, ...) is left untouched.
    Returns whether the specific key being written already existed.
    """
    existing = _read_data(path)
    if section is None:
        key = next(iter(new_data))
        overwritten = key in existing
        existing.update(new_data)
    else:
        new_section = new_data[section]
        key = next(iter(new_section))
        target_section = existing.setdefault(section, {})
        overwritten = key in target_section
        target_section.update(new_section)
    _write_data(path, existing)
    return overwritten


def add_list_entry(path: Path, section: str, entry: str) -> bool:
    """Appends `entry` (a path string, relative to `path`'s own
    directory — the same resolution rule config/includes.py uses for
    include: itself) to that list section in `path`, unless an entry
    already there resolves to the same file. Read-merge-write like
    merge_write(), but for a list section (include:) instead of a dict
    one — every other key in the file is left untouched. Returns whether
    an entry was actually added."""
    existing = _read_data(path)
    items = existing.setdefault(section, [])
    if not isinstance(items, list):
        raise OSError(_("{section}: in {path} is not a list — refusing to touch it")
                      .format(section=section, path=path))
    base_dir = path.parent
    target = (base_dir / entry).resolve()
    for existing_entry in items:
        existing_str = existing_entry if isinstance(existing_entry, str) \
            else (existing_entry or {}).get('path')
        if existing_str and (base_dir / existing_str).resolve() == target:
            return False
    items.append(entry)
    _write_data(path, existing)
    return True


def upsert_clone_placement(path: Path, entry: Dict[str, Any]) -> bool:
    """Read-merge-write like merge_write()/add_list_entry(), but for
    clone_placements: — a list of dicts matched by their own 'name' key,
    not by list membership: an entry whose name already exists gets
    REPLACED in place (same position), a new name gets appended. Every
    other key in the file (cells:, include:, extract_profiles:, ...) is
    left untouched."""
    existing = _read_data(path)
    items = existing.setdefault("clone_placements", [])
    if not isinstance(items, list):
        raise OSError(_("clone_placements: in {path} is not a list — refusing to touch it")
                      .format(path=path))
    overwritten = False
    for i, existing_entry in enumerate(items):
        if isinstance(existing_entry, dict) and existing_entry.get("name") == entry["name"]:
            items[i] = entry
            overwritten = True
            break
    if not overwritten:
        items.append(entry)
    _write_data(path, existing)
    return overwritten



def display_path(path: Path) -> str:
    """Path shown in labels: relative to PROJECT_ROOT when possible (the
    Files dock's tree is rooted there), absolute otherwise (a file
    outside that tree)."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def set_combo_items(combo: QComboBox, items: List[str]) -> None:
    """Replace a combo's items while preserving the current text and
    blocking selection signals around the repopulation (blockSignals) —
    so an in-progress typed value survives a refresh instead of being
    wiped, the same reason the tree/bulk-edit docks guard against
    resetting user input."""
    current_text = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(items)
    combo.setCurrentText(current_text)
    combo.blockSignals(False)


def configure_searchable(combo: QComboBox) -> None:
    """Turns a plain editable QComboBox into a filter-as-you-type search
    box. Qt's own default completer for an editable combo only matches
    from the start of the string, which isn't enough once there are
    dozens of nets/roles on a real board (2026-08-02: "сети стоит
    сделать выпадашками (комбобоксами с поиском)"). NoInsert keeps this
    a picker, not a whitelist — typed text that isn't in the list is
    still accepted as the field's value, it just doesn't get added as a
    new permanent entry."""
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    completer = combo.completer()
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)


def show_message(label: QLabel, text: str, style: str = "",
                 log: Optional[logging.Logger] = None) -> None:
    """Sets an inline status label AND mirrors the message into the Log
    dock (see gui/docks/log_panel.py) at the matching level, so error/
    warning messages survive after the label itself gets overwritten by
    the next action — requested live 2026-08-01 ("для списка ошибок
    сделать внизу отдельное окошко"). `style` is one of
    ERROR_STYLE/WARN_STYLE/SUCCESS_STYLE ('' -> plain info); the
    caller's logger is passed through so log records keep the source
    dock's own logger name."""
    label.setStyleSheet(style)
    label.setText(text)
    if not text:
        return
    record_log = log if log is not None else logger
    if style == ERROR_STYLE:
        record_log.error(text)
    elif style == WARN_STYLE:
        record_log.warning(text)
    else:
        record_log.info(text)
