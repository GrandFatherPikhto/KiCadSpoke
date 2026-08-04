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

from .. import yaml_io

logger = logging.getLogger(__name__)

# Project root — used by display_path() below to show paths relative to it
# when possible. Used to also be where FilePickerDock's file-tree was
# rooted (gui/docks/file_picker.py, removed 2026-08-03 — see
# handoff_2026_08_03_gui_tree_risks_resolved.md — replaced by ConfigTreeDock's
# "Open Root file" action, a plain QFileDialog with no directory browser).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Message-label styles — one definition shared by every dock's status
# label, instead of each dock declaring the same three CSS color strings.
ERROR_STYLE = "color: #a00;"
WARN_STYLE = "color: #a60;"
SUCCESS_STYLE = "color: #070;"


def _read_data(path: Path) -> dict:
    """Read an existing config file's YAML/JSON content (or {} when it
    doesn't exist yet). Raises OSError on read/parse errors instead of
    returning {} — the merge-write helpers are on the docks' write path,
    where a broken file must surface to the user, not be silently treated
    as empty (unlike gui/yaml_io.load_data). FIXED (2026-08-04): a malformed
    file used to raise the raw yaml.YAMLError/json.JSONDecodeError instead
    — neither is an OSError, so every caller's `except OSError` (e.g.
    PlacerDock._do_save's, written against exactly this docstring's
    promise) missed it, and the raw exception propagated uncaught out of a
    Qt slot, which PyQt6 aborts the whole process on by default. Found
    live: Placer's Save crashed the entire GUI over one stray character in
    an unrelated part of the target YAML file."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return (json.load(f) if path.suffix.lower() == ".json" else yaml.safe_load(f)) or {}
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            raise OSError(_("{path} is not valid {kind}: {error}").format(
                path=path, kind="JSON" if path.suffix.lower() == ".json" else "YAML", error=e)) from e


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


def upsert_list_entry(path: Path, section: str, entry: Dict[str, Any], key: str = "name") -> bool:
    """Read-merge-write like merge_write()/add_list_entry(), but for a list
    section whose entries are dicts matched by their own `key` field, not
    by list membership: an entry whose key already exists gets REPLACED in
    place (same position), a new key gets appended. Every other top-level
    key in the file (cells:, include:, extract_profiles:, ...) is left
    untouched. Shared shape for clone_placements: (see
    upsert_clone_placement) and thermal_via_arrays: (ConfigTreeDock's Add
    thermal via pad, 2026-08-03) — both are "list of named dict entries"
    sections in exactly this way."""
    existing = _read_data(path)
    items = existing.setdefault(section, [])
    if not isinstance(items, list):
        raise OSError(_("{section}: in {path} is not a list — refusing to touch it")
                      .format(section=section, path=path))
    overwritten = False
    for i, existing_entry in enumerate(items):
        if isinstance(existing_entry, dict) and existing_entry.get(key) == entry.get(key):
            items[i] = entry
            overwritten = True
            break
    if not overwritten:
        items.append(entry)
    _write_data(path, existing)
    return overwritten


def upsert_clone_placement(path: Path, entry: Dict[str, Any]) -> bool:
    """clone_placements:-specific name kept for the existing call sites/
    tests — see upsert_list_entry, the general form this now delegates to."""
    return upsert_list_entry(path, "clone_placements", entry)


def _include_entry_target(entry: Any, base_dir: Path) -> Optional[Path]:
    """Resolved path an include: entry (string or {path:, enabled:} dict)
    points at, or None for a malformed entry — shared by add_include()/
    disable_include() for matching an existing entry against a target
    file, same resolution rule config/includes.py's _parse_include_entry
    uses."""
    entry_str = entry if isinstance(entry, str) else (entry or {}).get("path")
    return (base_dir / entry_str).resolve() if entry_str else None


def add_include(path: Path, entry: str) -> bool:
    """Like add_list_entry(path, "include", entry), but if an entry already
    there resolves to the same file and is currently disabled
    (enabled: false), RE-ENABLES it instead of adding a duplicate line —
    ConfigTreeDock's Add-file action (2026-08-03) re-including a file
    previously removed via disable_include() below should undo that, not
    pile up a second entry for the same path. Returns whether anything
    changed (added or re-enabled)."""
    existing = _read_data(path)
    items = existing.setdefault("include", [])
    if not isinstance(items, list):
        raise OSError(_("include: in {path} is not a list — refusing to touch it").format(path=path))
    base_dir = path.parent
    target = (base_dir / entry).resolve()
    for i, existing_entry in enumerate(items):
        if _include_entry_target(existing_entry, base_dir) != target:
            continue
        if isinstance(existing_entry, dict) and existing_entry.get("enabled") is False:
            items[i] = existing_entry["path"]  # re-enabled == plain string form again
            _write_data(path, existing)
            return True
        return False  # already there and already enabled
    items.append(entry)
    _write_data(path, existing)
    return True


def disable_include(path: Path, target: Path) -> bool:
    """Soft-removes an include: entry pointing at `target` — sets
    enabled: false rather than erasing the line (ConfigTreeDock's Remove-
    file action, 2026-08-03: "стирать файл — это экстремизм", toggle-able
    back via add_include() above, not a destructive delete). Converts a
    plain string entry into the {path:, enabled: false} mapping form
    add_include()/config/includes.py's _parse_include_entry already
    understand. Returns whether an entry was found and changed."""
    existing = _read_data(path)
    items = existing.get("include") or []
    base_dir = path.parent
    for i, existing_entry in enumerate(items):
        if _include_entry_target(existing_entry, base_dir) != target.resolve():
            continue
        if isinstance(existing_entry, dict) and existing_entry.get("enabled") is False:
            return False  # already disabled
        entry_str = existing_entry if isinstance(existing_entry, str) else existing_entry["path"]
        items[i] = {"path": entry_str, "enabled": False}
        _write_data(path, existing)
        return True
    return False


# include: only ever merges these top-level keys from an included file
# (config/includes.py's _LIST_SECTIONS/_DICT_SECTIONS) — everything else is
# fatal there (no defined multi-file merge behaviour). A file assigned a
# GUI role (or added as an include: target) can perfectly well ALSO be a
# full root config in its own right (registry_path/schematic_dir/...) if
# it was set up that way before — found live 2026-08-01: writing include:
# blindly in that case leaves the including file unloadable next time
# anything reads it.
INCLUDABLE_KEYS = frozenset(
    {"rules", "clone_placements", "cells", "points", "extract_profiles", "clone_profiles", "include"})


def non_includable_keys(path: Path) -> set:
    """Top-level keys in `path` that include: can't merge — see
    INCLUDABLE_KEYS. Shared by ExtractDock (after a successful extract,
    wiring the Placer file's include:) and ConfigTreeDock's Add-file
    action (2026-08-03, must not offer to include a file that would make
    the including file unloadable)."""
    return set(yaml_io.load_data(path).keys()) - INCLUDABLE_KEYS


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


# Chars a pure "=====" / "-----" separator/border line is made of — skipped
# when picking the label's one-line preview (kicadstamp.exceptions' FATAL
# ERROR box opens with a "=" * 70 border, which isn't a useful preview on
# its own).
_SEPARATOR_CHARS = set("=-*_~")


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and set(stripped) - _SEPARATOR_CHARS:
            return stripped
    return ""


def show_message(label: QLabel, text: str, style: str = "",
                 log: Optional[logging.Logger] = None) -> None:
    """Sets an inline status label AND mirrors the FULL message into the
    Log dock (see gui/docks/log_panel.py) at the matching level, so error/
    warning messages survive after the label itself gets overwritten by
    the next action — requested live 2026-08-01 ("для списка ошибок
    сделать внизу отдельное окошко"). `style` is one of
    ERROR_STYLE/WARN_STYLE/SUCCESS_STYLE ('' -> plain info); the
    caller's logger is passed through so log records keep the source
    dock's own logger name.

    The label itself only ever shows one line (2026-08-04: some backend
    errors, e.g. kicadstamp.exceptions' FATAL ERROR box, are a multi-line
    "=" * 70-bordered block — dumping that whole thing into a word-wrapped
    label was blowing the dock up so tall the tab no longer fit on screen).
    Anything past the first non-blank line is truncated, with the full
    text still one hover away via the tooltip and, as before, in the Log
    dock in full."""
    label.setStyleSheet(style)
    preview = _first_meaningful_line(text)
    if preview != text.strip():
        preview += _(" (see Log for details)")
    label.setText(preview)
    label.setToolTip(text)
    if not text:
        return
    record_log = log if log is not None else logger
    if style == ERROR_STYLE:
        record_log.error(text)
    elif style == WARN_STYLE:
        record_log.warning(text)
    else:
        record_log.info(text)
