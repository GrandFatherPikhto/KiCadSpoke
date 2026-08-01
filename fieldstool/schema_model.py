# fieldstool/schema_model.py
"""
load_schematic_components() — flattens the whole schematic hierarchy
(discovery.py + blocks.py) into one row per REFDES, for the GUI's
Role/Cluster tree (fieldstool/gui/tree.py) and for staging edits by ref.
A (symbol ...) block can carry several refdes (multi-instance sheet) or a
refdes can span several blocks (multi-unit symbol, see set_fields.py's
module docstring) — this expands/collapses both into "one row per ref"
since that's the unit a human picks components by, even though the
underlying edit is block-level.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .blocks import find_property_value_span, iter_symbol_blocks
from .discovery import walk_schematic_hierarchy


@dataclass
class SchematicComponent:
    ref: str
    role: Optional[str]
    cluster: Optional[str]
    file: str
    block_start: int  # identifies which SymbolBlock this ref's shown values came from
    divergent: bool    # True if this ref spans multiple blocks with disagreeing Role/Cluster
                       # (multi-unit symbol whose per-unit copies were never kept in sync —
                       # schema allows it, nothing enforces it, see set_fields.py)


def load_schematic_components(root_sheet: str) -> List[SchematicComponent]:
    files = walk_schematic_hierarchy(root_sheet)
    # ref -> list of (role, cluster, file, block_start), one entry per block containing it
    by_ref: Dict[str, list] = {}
    for f in files:
        text = Path(f).read_text(encoding="utf-8")
        for block in iter_symbol_blocks(f, text):
            if not block.refs:
                continue
            span_text = text[block.start:block.end]
            role_span = find_property_value_span(span_text, "Role")
            cluster_span = find_property_value_span(span_text, "Cluster")
            role = span_text[role_span[0]:role_span[1]] if role_span else None
            cluster = span_text[cluster_span[0]:cluster_span[1]] if cluster_span else None
            for ref in block.refs:
                by_ref.setdefault(ref, []).append((role, cluster, f, block.start))

    components = []
    for ref, entries in by_ref.items():
        role, cluster, file, block_start = entries[0]
        divergent = any((r, c) != (role, cluster) for r, c, _, _ in entries[1:])
        components.append(SchematicComponent(ref, role, cluster, file, block_start, divergent))
    return components
