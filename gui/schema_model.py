# gui/schema_model.py
"""
load_schematic_components() — flattens the whole schematic hierarchy
(kicadstamp.schematic_discovery + kicadstamp.schematic_blocks) into one
row per REFDES, for the main GUI's Components tree in "Not yet applied"
mode (gui/docks/role_cluster_tree.py, reading gui.fieldstool_window.
MainWindow's own self._components) and for staging edits by ref. GUI-only
(fieldstool_cli.py never needs a per-ref flattened view) — that's why this
lives in gui/, not alongside the kicadstamp.schematic_* modules it reads.
A (symbol ...) block can carry several refdes (multi-instance sheet) or a
refdes can span several blocks (multi-unit symbol, see kicadstamp.
schematic_set_fields's module docstring) — this expands/collapses both
into "one row per ref" since that's the unit a human picks components by,
even though the underlying edit is block-level.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from kicadstamp.schematic_blocks import find_property_value_span
from kicadstamp.schematic_discovery import load_schematic_tree


@dataclass
class SchematicComponent:
    ref: str
    role: Optional[str]
    cluster: Optional[str]
    file: str
    block_start: int  # identifies which SymbolBlock this ref's shown values came from
    divergent: bool    # True if this ref spans multiple blocks with disagreeing Role/Cluster
                       # (multi-unit symbol whose per-unit copies were never kept in sync —
                       # schema allows it, nothing enforces it, see schematic_set_fields.py)
    symbol_uuids: tuple = ()  # top-level (uuid ...) of the SymbolBlock(s) carrying this
                              # ref — the symbol's own identity. The board side exposes the
                              # same uuid as fp.sheet_path.path[-1] (recon confirmed:
                              # 279/279 on 3CH-AWG-TIA), which lets Pending changes verify a
                              # refdes means the SAME symbol on both sides instead of trusting
                              # the refdes string blindly (see gui/docks/pending.py).


def load_schematic_components(root_sheet: str) -> List[SchematicComponent]:
    _files, file_texts, all_blocks = load_schematic_tree(root_sheet)
    # ref -> list of (role, cluster, file, block_start, symbol_uuid), one entry per block containing it
    by_ref: Dict[str, list] = {}
    for block in all_blocks:
        if not block.refs:
            continue
        span_text = file_texts[block.file][block.start:block.end]
        role_span = find_property_value_span(span_text, "Role")
        cluster_span = find_property_value_span(span_text, "Cluster")
        role = span_text[role_span[0]:role_span[1]] if role_span else None
        cluster = span_text[cluster_span[0]:cluster_span[1]] if cluster_span else None
        for ref in block.refs:
            # KiCad's own convention: a reference starting with "#" (power
            # symbols/PWR_FLAG — #PWR01, #FLG01, ...) marks "excluded from
            # board" — no footprint ever exists for these, so they never
            # show up in the live PCB tree (kipy only enumerates real
            # footprints) and can't usefully carry a Role/Cluster tag.
            # iter_symbol_blocks() reads every (symbol ...) instance
            # unconditionally (it's shared with schematic_set_fields.py/
            # schematic_rename_fields.py, which must still be able to
            # target one directly if ever asked) — this GUI-only "one row
            # per ref" view is where the noise actually gets filtered
            # (found live 2026-08-03: 171 "#FLG*" rows in one real board's
            # schematic view, none selectable on the PCB).
            if ref.startswith("#"):
                continue
            by_ref.setdefault(ref, []).append((role, cluster, block.file, block.start, block.uuid))

    components = []
    for ref, entries in by_ref.items():
        role, cluster, file, block_start, _ = entries[0]
        divergent = any((r, c) != (role, cluster) for r, c, _, _, _ in entries[1:])
        symbol_uuids = tuple(sorted({u for _, _, _, _, u in entries if u}))
        components.append(SchematicComponent(
            ref, role, cluster, file, block_start, divergent, symbol_uuids))
    return components


@dataclass
class SchematicInstance:
    """One PLACED instance of a symbol — the per-instance (path ...) entry
    from (instances ...) resolved to the block's Role/Cluster. The unit the
    full-path diff (compute_pending_edits with path_index) matches against:
    a board footprint's sheet_path.path equals exactly one of these keys."""
    ref: str
    role: Optional[str]
    cluster: Optional[str]
    file: str
    block_start: int


def _full_key(inst_path: tuple, top_uuid: str) -> tuple:
    """The key a board fp.sheet_path.path equals: the schematic (instances ...)
    path minus its root uuid, plus the block's top-level uuid. For a root-sheet
    symbol inst_path is (root,) -> key is just (top_uuid,). Empirically this
    joins 358/364 footprints on 3CH-AWG-TIA (the 6 misses are board-only)."""
    return inst_path[1:] + (top_uuid,) if len(inst_path) > 1 else (top_uuid,)


def load_schematic_instances(root_sheet: str) -> Dict[tuple, SchematicInstance]:
    """Full-path index: board `fp.sheet_path.path` -> the schematic instance
    that footprint physically is, regardless of whether the two sides agree
    on the refdes (re-annotation desync). Key = _full_key(instances path, block
    uuid). This is what lets Pending changes diff a re-annotated board's
    footprints against the correct schematic symbol instead of silently
    dropping them as "board-only" or pairing them by a stale refdes."""
    _files, file_texts, all_blocks = load_schematic_tree(root_sheet)
    index: Dict[tuple, SchematicInstance] = {}
    for block in all_blocks:
        if not block.instances or not block.uuid:
            continue
        span_text = file_texts[block.file][block.start:block.end]
        role_span = find_property_value_span(span_text, "Role")
        cluster_span = find_property_value_span(span_text, "Cluster")
        role = span_text[role_span[0]:role_span[1]] if role_span else None
        cluster = span_text[cluster_span[0]:cluster_span[1]] if cluster_span else None
        for inst_path, ref in block.instances:
            if ref.startswith("#"):
                continue
            key = _full_key(inst_path, block.uuid)
            index.setdefault(key, SchematicInstance(ref, role, cluster,
                                                    block.file, block.start))
    return index
