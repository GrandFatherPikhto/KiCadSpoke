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


def load_schematic_components(root_sheet: str) -> List[SchematicComponent]:
    _files, file_texts, all_blocks = load_schematic_tree(root_sheet)
    # ref -> list of (role, cluster, file, block_start), one entry per block containing it
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
            by_ref.setdefault(ref, []).append((role, cluster, block.file, block.start))

    components = []
    for ref, entries in by_ref.items():
        role, cluster, file, block_start = entries[0]
        divergent = any((r, c) != (role, cluster) for r, c, _, _ in entries[1:])
        components.append(SchematicComponent(ref, role, cluster, file, block_start, divergent))
    return components
