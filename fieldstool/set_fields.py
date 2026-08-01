# fieldstool/set_fields.py
"""
Bulk-set Role/Cluster (or any field) by refdes: YAML config maps
refdes -> {field: value}. Ported from tools/apply_role_cluster.py's
main() steps 3-6 (2026-08-01 fold-in), split out of that script's single
main() into functions returning a plan instead of printing/exiting
inline, so both fieldstool_cli.py and (later) fieldstool/gui can call it.

Two ways one refdes shows up in a file, both handled (see
iter_symbol_blocks/blocks.py):
  - Multi-unit symbol (e.g. a dual op-amp) — one refdes sits in SEVERAL
    separate (symbol ...) blocks (one per unit), each with its own copy
    of the field — ALL of that refdes's blocks get edited.
  - Multi-instance sheet (Channel_1/2/3 all instancing one
    channel_tpl.kicad_sch) — SEVERAL refdes sit in ONE (symbol ...) block
    (the field is shared across the whole block). If the config asks for
    DIFFERENT values for two such refdes, the format physically cannot
    express that — this is a fatal FieldsToolError, not a silent pick of
    one of the two.
"""
import difflib
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from .blocks import (PROPERTY_BLOCK_TEMPLATE, SymbolBlock, escape_sexp_string,
                     find_insertion_point, find_property_value_span, find_symbol_at,
                     iter_symbol_blocks)
from .discovery import walk_schematic_hierarchy
from .editing import Edit, EditReport
from .exceptions import FieldsToolError


def load_set_config(path: Path) -> Tuple[str, Dict[str, Dict[str, str]]]:
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    root_sheet = data.get('root_sheet')
    if not root_sheet:
        raise FieldsToolError("config has no root_sheet")
    fields = data.get('fields') or {}
    if not fields:
        raise FieldsToolError("config's fields: is empty (or missing)")
    return root_sheet, fields


def _parse_all_blocks(files: List[str]) -> Tuple[Dict[str, str], List[SymbolBlock]]:
    file_texts: Dict[str, str] = {}
    all_blocks: List[SymbolBlock] = []
    for f in files:
        with open(f, encoding='utf-8', newline='') as fh:
            file_texts[f] = fh.read()
        all_blocks.extend(iter_symbol_blocks(f, file_texts[f]))
    return file_texts, all_blocks


def plan_set_edits(
    config_path: Path,
) -> Tuple[Dict[str, List[Edit]], Dict[str, str], List[EditReport]]:
    """Resolves config_path (root_sheet + fields:) against the live
    schematic tree. Thin wrapper around plan_set_edits_for_root() — see
    there for the actual planning logic (shared with fieldstool/gui's
    Apply, which stages refdes -> {field: value} in memory rather than
    writing a temporary YAML config just to reuse this)."""
    base = config_path.parent
    root_sheet, fields_cfg = load_set_config(config_path)
    root_path = base / root_sheet
    if not root_path.is_file():
        raise FieldsToolError(f"root_sheet {root_sheet!r} not found ({root_path})")
    return plan_set_edits_for_root(str(root_path), fields_cfg)


def plan_set_edits_for_root(
    root_sheet_path: str, fields_cfg: Dict[str, Dict[str, str]],
) -> Tuple[Dict[str, List[Edit]], Dict[str, str], List[EditReport]]:
    """Same planning as plan_set_edits(), given an already-resolved root
    sheet path and an in-memory fields_cfg (refdes -> {field: value})
    instead of a YAML config file. Raises FieldsToolError with every
    problem listed at once (unknown refdes, multi-instance conflicts) —
    nothing is planned if anything is wrong, same "stop before touching
    anything" discipline the rest of this project uses."""
    files = walk_schematic_hierarchy(root_sheet_path)
    file_texts, all_blocks = _parse_all_blocks(files)

    ref_to_blocks: Dict[str, List[SymbolBlock]] = {}
    for b in all_blocks:
        for r in b.refs:
            ref_to_blocks.setdefault(r, []).append(b)

    missing = [r for r in fields_cfg if r not in ref_to_blocks]
    if missing:
        known = sorted(ref_to_blocks.keys())
        lines = []
        for r in missing:
            suggestion = difflib.get_close_matches(r, known, n=1)
            hint = f" (did you mean {suggestion[0]!r}?)" if suggestion else ""
            lines.append(f"  {r} — not found in any .kicad_sch reachable from {root_sheet_path}{hint}")
        raise FieldsToolError("unknown refdes in config:\n" + "\n".join(lines))

    # bid -> {field: (value, first_requesting_refdes)}
    blocks_by_id = {b.id: b for b in all_blocks}
    blocks_fields: Dict[Tuple[str, int], Dict[str, Tuple[str, str]]] = {}
    conflicts = []
    for ref, fv in fields_cfg.items():
        for b in ref_to_blocks[ref]:
            per_block = blocks_fields.setdefault(b.id, {})
            for field, value in fv.items():
                value = str(value)
                if field in per_block and per_block[field][0] != value:
                    conflicts.append((b, field, per_block[field][1], per_block[field][0], ref, value))
                else:
                    per_block[field] = (value, ref)
    if conflicts:
        lines = []
        for b, field, ref_a, val_a, ref_b, val_b in conflicts:
            lines.append(
                f"  {Path(b.file).name}: {ref_a!r} and {ref_b!r} share one (symbol ...) block "
                f"(multi-instance of one sheet), but ask for different {field}: "
                f"{val_a!r} vs {val_b!r}"
            )
        raise FieldsToolError(
            "conflict: a shared (symbol ...) block can't give different instances different "
            "values of the same field:\n" + "\n".join(lines)
        )

    edits_by_file: Dict[str, List[Edit]] = {f: [] for f in files}
    report: List[EditReport] = []
    for bid, per_block in blocks_fields.items():
        b = blocks_by_id[bid]
        span_text = file_texts[b.file][b.start:b.end]
        for field, (value, _ref) in per_block.items():
            escaped = escape_sexp_string(value)
            existing = find_property_value_span(span_text, field)
            if existing:
                vs, ve = existing
                old_value = span_text[vs:ve]
                if old_value == escaped:
                    continue
                edits_by_file[b.file].append((b.start + vs, b.start + ve, escaped))
                report.append(EditReport(b.file, sorted(b.refs), field, old_value, value, "replace"))
            else:
                insert_at, prefix = find_insertion_point(span_text)
                x, y = find_symbol_at(span_text)
                block_text = PROPERTY_BLOCK_TEMPLATE.format(
                    prefix=prefix, field=field, value=escaped, x=x, y=y)
                edits_by_file[b.file].append((b.start + insert_at, b.start + insert_at, block_text))
                report.append(EditReport(b.file, sorted(b.refs), field, None, value, "insert"))

    return edits_by_file, file_texts, report
