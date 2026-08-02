# kicadstamp/schematic_rename_fields.py
"""
Bulk-rename a field's VALUE, project-wide, without enumerating refdes:
YAML config maps field -> {old_value: new_value}. New (2026-08-01,
alongside the schematic_set_fields.py port) — the actual reason this tool
exists beyond what tools/apply_role_cluster.py already did.

Simpler than schematic_set_fields.py in one respect: SET has to detect and
fatal on a real conflict (two refdes sharing one (symbol ...) block via a
multi-instance sheet, asking for different values — the format can't
express that). RENAME never hits this, because it always writes the SAME
new value to every block whose CURRENT value matches — there's no
per-ref ambiguity to resolve. It also never inserts a new property (a
block with no such field simply can't match an old_value, since no field
means no current value to compare against) — every EditReport this
produces has kind == "replace".
"""
from pathlib import Path

from .exceptions import FieldsToolError
from .schematic_blocks import escape_sexp_string, find_property_value_span, unescape_sexp_string
from .schematic_config import load_fields_config
from .schematic_discovery import load_schematic_tree
from .schematic_editing import Edit, EditReport


def load_rename_config(path: Path) -> tuple[str, dict[str, dict[str, str]]]:
    return load_fields_config(path, "renames")


def plan_rename_edits(
    config_path: Path,
) -> tuple[dict[str, list[Edit]], dict[str, str], list[EditReport], list[str]]:
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

    files, file_texts, all_blocks = load_schematic_tree(str(root_path))

    edits_by_file: dict[str, list[Edit]] = {f: [] for f in files}
    report: list[EditReport] = []
    matched_old_values = {field: set() for field in renames_cfg}

    for block in all_blocks:
        if not block.refs:
            continue
        span_text = file_texts[block.file][block.start:block.end]
        for field, value_map in renames_cfg.items():
            existing = find_property_value_span(span_text, field)
            if not existing:
                continue
            vs, ve = existing
            escaped_current = span_text[vs:ve]
            # A .kicad_sch stores a quote as an escaped backslash-quote, so
            # unescape before comparing to the raw (unescaped) config key.
            current_value = unescape_sexp_string(escaped_current)
            if current_value not in value_map:
                continue
            matched_old_values[field].add(current_value)
            new_value = str(value_map[current_value])
            escaped = escape_sexp_string(new_value)
            if escaped_current == escaped:
                continue  # already renamed (idempotent re-run)
            edits_by_file[block.file].append((block.start + vs, block.start + ve, escaped))
            report.append(EditReport(
                block.file, sorted(block.refs), field, current_value, new_value, "replace"))

    unmatched: list[str] = []
    for field, value_map in renames_cfg.items():
        for old_value in value_map:
            if old_value not in matched_old_values[field]:
                unmatched.append(f"{field}: {old_value!r}")

    return edits_by_file, file_texts, report, unmatched
