# kicadstamp/config/points.py
"""
points.py — the Point dataclass, kept out of models.py deliberately (see
handoff_2026_07_31_consolidated.md §7 item 5): Point is a named, reusable
anchor definition, a config-schema entity like Cell/Rule/ClonePlacement, but
one the project expects more of over time — kept in its own small file
rather than folded into the growing models.py.

No validation logic here — same split as the rest of config/ (loader.py
validates, models.py only describes shape).
"""
from dataclasses import dataclass



@dataclass
class Point:
    """
    A named, reusable anchor + optional shift, no rotation (rotation is
    always an operation applied by whoever USES a point, not a property of
    the point itself — see handoff_2026_07_31_consolidated.md).

    Exactly one of {anchor_ref, anchor_role, anchor_point, xy} is the base:
      - anchor_ref/anchor_role(+anchor_sheet+anchor_cluster)/anchor_pad —
        same fields, same resolution, as Rule/ClonePlacement/
        ThermalViaArrayConfig. No {placeholder} substitution in anchor_sheet
        (Point has no params field — same convention as Rule/
        ThermalViaArrayConfig, not ClonePlacement's richer one).
      - anchor_point — chain to another point by name.
      - xy — literal absolute board coordinate, no anchor at all.
    shift_x_mm/shift_y_mm layer on top of the first three bases (not on top
    of xy — fatal if both are set, just edit the literal coordinate
    instead), same board-absolute-mm convention as ManualSpoke.shift_x_mm/
    shift_y_mm (not relative to any anchor rotation, there is none here).
    """
    name: str
    anchor_ref: str | None = None
    anchor_role: str | None = None
    anchor_sheet: str | None = None
    anchor_cluster: str | None = None
    anchor_pad: str | None = None
    anchor_point: str | None = None
    xy: tuple[float, float] | None = None
    shift_x_mm: float = 0.0
    shift_y_mm: float = 0.0
