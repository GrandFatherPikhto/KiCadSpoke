# fieldstool/rename_fields.py
"""
Bulk-rename a field's VALUE, project-wide, without enumerating refdes:
YAML config maps field -> {old_value: new_value}. New (2026-08-01,
alongside the set_fields.py port) — the actual reason fieldstool exists
beyond what tools/apply_role_cluster.py already did.

Simpler than set_fields.py in one respect: SET has to detect and fatal on
a real conflict (two refdes sharing one (symbol ...) block via a
multi-instance sheet, asking for different values — the format can't
express that). RENAME never hits this, because it always writes the SAME
new value to every block whose CURRENT value matches — there's no
per-ref ambiguity to resolve. It also never inserts a new property (a
block with no such field simply can't match an old_value, since no field
means no current value to compare against) — every EditReport this
produces has kind == "replace".
"""
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from .blocks import escape_sexp_string, find_property_value_span, iter_symbol_blocks
from .discovery import walk_schematic_hierarchy
from .editing import Edit, EditReport
from .exceptions import FieldsToolError


def load_rename_config(path: Path) -> Tuple[str, Dict[str, Dict[str, str]]]:
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    root_sheet = data.get('root_sheet')
    if not root_sheet:
        raise FieldsToolError("config has no root_sheet")
    renames = data.get('renames') or {}
    if not renames:
        raise FieldsToolError("config's renames: is empty (or missing)")
    return root_sheet, renames


def plan_rename_edits(
    config_path: Path,
) -> Tuple[Dict[str, List[Edit]], Dict[str, str], List[EditReport], List[str]]:
    """Resolves config_path (root_sheet + renames:) against the live
    schematic tree. Returns (edits_by_file, file_texts, report,
    unmatched_old_values) — unmatched_old_values (renames entries that
    matched nothing anywhere) is returned for the CALLER to warn about,
    not raised as fatal: an unmatched old_value is just as likely a
    harmless re-run (already renamed by a previous --write — renaming is
    naturally idempotent, unlike set's refdes list) as a real typo."""
    base = config_path.parent
    root_sheet, renames_cfg = load_rename_config(config_path)
    root_path = base / root_sheet
    if not root_path.is_file():
        raise FieldsToolError(f"root_sheet {root_sheet!r} not found ({root_path})")

    files = walk_schematic_hierarchy(str(root_path))
    file_texts: Dict[str, str] = {}
    for f in files:
        with open(f, encoding='utf-8', newline='') as fh:
            file_texts[f] = fh.read()

    edits_by_file: Dict[str, List[Edit]] = {f: [] for f in files}
    report: List[EditReport] = []
    matched_old_values = {field: set() for field in renames_cfg}

    for f in files:
        text = file_texts[f]
        for block in iter_symbol_blocks(f, text):
            if not block.refs:
                continue
            span_text = text[block.start:block.end]
            for field, value_map in renames_cfg.items():
                existing = find_property_value_span(span_text, field)
                if not existing:
                    continue
                vs, ve = existing
                current_value = span_text[vs:ve]
                if current_value not in value_map:
                    continue
                matched_old_values[field].add(current_value)
                new_value = str(value_map[current_value])
                escaped = escape_sexp_string(new_value)
                if current_value == escaped:
                    continue  # already renamed (idempotent re-run)
                edits_by_file[f].append((block.start + vs, block.start + ve, escaped))
                report.append(EditReport(
                    f, sorted(block.refs), field, current_value, new_value, "replace"))

    unmatched: List[str] = []
    for field, value_map in renames_cfg.items():
        for old_value in value_map:
            if old_value not in matched_old_values[field]:
                unmatched.append(f"{field}: {old_value!r}")

    return edits_by_file, file_texts, report, unmatched
