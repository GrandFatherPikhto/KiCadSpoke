# kicadstamp/placement/services/point_resolver.py
"""
point_resolver.py — resolves a Point (see config/points.py) to an absolute
board position, and, when eligible, the live footprint it resolved through.

Two grades, same split as Rule (manual_position_calculator.py) and
ClonePlacement (clone_position_calculator.py):
  - resolve_point_anchor_ref — lightweight, identity-only, used by
    dependency_order.py to build the dependency graph before any real
    planning happens.
  - resolve_point — the real thing, called once per Point in dependency
    order (see planner.py's plan_item(), kind == 'point' branch), producing
    a ResolvedPoint that other items' anchor_point references then read.
"""
import logging
from dataclasses import dataclass
from typing import Dict, Optional

from kipy.board_types import FootprintInstance
from kipy.geometry import Vector2

from ...config import Point
from ...kicad.adapter import KiCadBoardAdapter
from ...utils.units import MM
from ...exceptions import ValidationError, format_fatal_error
from .component_resolver import (
    resolve_anchor_identity,
    resolve_anchor_pad_position,
    resolve_footprint_by_ref,
)
from .clone_role_resolver import resolve_footprint_by_role
from ...i18n import _

logger = logging.getLogger(__name__)


@dataclass
class ResolvedPoint:
    """A Point resolved to an absolute board position, plus — only when no
    shift has been applied anywhere in its anchor_point chain — the live
    footprint it resolved through. Rule/ThermalViaArrayConfig need `footprint`
    to look up a specific named pad from (spoke.pad/tva.pad); ClonePlacement
    only ever needs `position`. `footprint` is None once a shift is applied,
    since the point is then no longer physically AT that footprint — same
    "footprint-eligible" rule config/loader.py already enforces statically at
    load time for Rule/ThermalViaArrayConfig's anchor_point (see
    _point_is_footprint_eligible there); this is where that guarantee is
    actually produced.
    """
    position: Vector2
    footprint: Optional[FootprintInstance]


def resolve_point_anchor_ref(adapter: KiCadBoardAdapter, point: Point, sheet_names=None) -> Optional[str]:
    """Lightweight, identity-only anchor resolution for a Point — mirrors
    resolve_rule_anchor_ref (manual_position_calculator.py) /
    resolve_clone_anchor_ref (clone_position_calculator.py). Used ONLY to
    build dependency_order.py's dependency graph, not to compute a real
    position — see resolve_point below for that."""
    # xy-literal (no anchor at all) correctly falls through
    # resolve_anchor_identity to None too — no dependency, goes to level 0.
    _sn = sheet_names or {}
    return resolve_anchor_identity(
        point.anchor_ref, point.anchor_role, point.anchor_point,
        lambda: resolve_footprint_by_role(
            adapter, point.anchor_role, point.anchor_sheet, point.anchor_cluster,
            _sn, label=_("point {name!r}").format(name=point.name),
        ),
    )


def resolve_point(adapter: KiCadBoardAdapter, point: Point,
                  resolved_points: Dict[str, ResolvedPoint], sheet_names=None) -> ResolvedPoint:
    """
    Resolves ONE Point's own definition into an absolute position (+
    footprint, when eligible). Called once per Point, in dependency order
    (planner.py's plan_item(), kind == 'point' branch — the caller is
    responsible for storing the result into resolved_points under
    point.name, same discipline as plan_item() for rule/clone).

    If this point chains via anchor_point, the referenced point is
    GUARANTEED already present in resolved_points — Kahn's algorithm
    (dependency_order.py) already ordered the graph so that whatever a point
    depends on is resolved first — so this is a plain dict lookup, no
    recursion needed here.
    """
    _sn = sheet_names or {}

    if point.anchor_point is not None:
        base = resolved_points[point.anchor_point]
        base_position = base.position
        base_footprint = base.footprint
    elif point.anchor_ref is not None or point.anchor_role is not None:
        if point.anchor_ref is not None:
            fp = resolve_footprint_by_ref(adapter, point.anchor_ref,
                                          _("point {name!r}").format(name=point.name))
        else:
            fp = resolve_footprint_by_role(
                adapter, point.anchor_role, point.anchor_sheet, point.anchor_cluster,
                _sn, label=_("point {name!r}").format(name=point.name),
            )
        if point.anchor_pad is not None:
            base_position = resolve_anchor_pad_position(
                adapter, fp, point.anchor_pad, _("point {name!r}").format(name=point.name))
            base_footprint = None  # pad-level anchor: no longer "the whole footprint"
        else:
            base_position = fp.position
            base_footprint = fp
    elif point.xy is not None:
        base_position = Vector2.from_xy(int(point.xy[0] * MM), int(point.xy[1] * MM))
        base_footprint = None
    else:
        # Unreachable if config/loader.py's _load_point validation ran —
        # kept as a loud failure, not a silent fallback, in case a Point is
        # ever constructed some other way (tests, future code paths).
        raise ValidationError(format_fatal_error(
            _("point {name!r} has no anchor").format(name=point.name),
            [_("set anchor_ref/anchor_role, anchor_point, or xy — should have been "
               "caught at load time (config/loader.py)")]
        ))

    shift = Vector2.from_xy(int(point.shift_x_mm * MM), int(point.shift_y_mm * MM))
    position = Vector2.from_xy(base_position.x + shift.x, base_position.y + shift.y)
    has_shift = point.shift_x_mm != 0.0 or point.shift_y_mm != 0.0
    footprint = None if has_shift else base_footprint

    return ResolvedPoint(position=position, footprint=footprint)
