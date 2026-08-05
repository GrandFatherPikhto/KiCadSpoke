# kicadstamp/template_selection.py
"""
template_selection.py — pure selection-geometry helpers for template
extraction. Split out of template_extraction.py during the T3.1 god-file
decomposition (behavior-preserving code move — see
handoff_2026_08_05_architecture_fixes_roadmap.md).

Contains only selection logic with no YAML/serialization concern:
  * point matching within POSITION_TOLERANCE_MM (KiCad does not require
    exact coordinate coincidence for electrical connectivity);
  * inflating the real bounding boxes of pads/vias with a small epsilon;
  * filtering selected tracks to those whose BOTH ends match something else
    in the selection;
  * resolving the extraction origin (bbox lower-left corner, or an explicit
    via/component).
"""
import logging
from typing import Any

from kipy.board_types import FootprintInstance, Via, Track
from kipy.geometry import Vector2

from .constants import POSITION_TOLERANCE_MM, ROLE_FIELD_NAME
from .exceptions import ValidationError, format_fatal_error
from .kicad.adapter import KiCadBoardAdapter
from .utils.units import MM
from .i18n import _

logger = logging.getLogger(__name__)


def _points_match(p1: Vector2, p2: Vector2, tol_mm: float = POSITION_TOLERANCE_MM) -> bool:
    return abs(p1.x - p2.x) / MM <= tol_mm and abs(p1.y - p2.y) / MM <= tol_mm


def _point_matches_any(point: Vector2, anchors: list[Vector2]) -> bool:
    return any(_points_match(point, a) for a in anchors)


_BBOX_EPSILON_MM = 0.001  # NOT a routing tolerance (the real bbox of via/pad
                          # already provides all the needed margin — via radius
                          # is usually an order of magnitude larger than any
                          # manual routing error). This is purely a defence
                          # against coordinate quantisation/float rounding when
                          # converting to nm, not a "how crookedly the track is
                          # attached" tolerance.


def _inflated_boxes(adapter: KiCadBoardAdapter, items: list[Any]) -> list[Any]:
    boxes = adapter.get_bounding_boxes(items)
    for b in boxes:
        if b is not None:
            b.inflate(int(_BBOX_EPSILON_MM * MM))
    return boxes


def _point_in_box(point: Vector2, box) -> bool:
    if box is None:
        return False
    return (box.pos.x <= point.x <= box.pos.x + box.size.x
            and box.pos.y <= point.y <= box.pos.y + box.size.y)


def _filter_tracks_within_selection(
    tracks: list[Track], footprints: list[FootprintInstance], vias: list[Via],
    adapter: KiCadBoardAdapter,
) -> list[Track]:
    """
    Keeps only tracks whose BOTH ends match something else in the selection.
    "Match" is not exact coordinate equality (KiCad does not require exact
    coincidence for electrical connectivity — connectivity is about copper
    overlap within the real via/pad footprint, not coordinate to the micron;
    manual routing almost never lands exactly at the centre), but rather that
    the track endpoint falls within the REAL bounding box of the corresponding
    via or pad (+ a small technological margin). For track‑to‑track joints
    (no via/pad between them) no margin is needed — there either the endpoint
    meets endpoint, or they are two different tracks.
    """
    all_pads = [p for fp in footprints for p in adapter.get_footprint_pads(fp)]
    pad_boxes = _inflated_boxes(adapter, all_pads)
    via_boxes = _inflated_boxes(adapter, vias)

    def endpoint_ok(point: Vector2, this_track: Track) -> bool:
        if any(_point_in_box(point, box) for box in via_boxes):
            return True
        if any(_point_in_box(point, box) for box in pad_boxes):
            return True
        for other in tracks:
            if other is this_track:
                continue
            if _points_match(point, other.start) or _points_match(point, other.end):
                return True
        return False

    kept = []
    for t in tracks:
        start_ok = endpoint_ok(t.start, t)
        end_ok = endpoint_ok(t.end, t)
        if start_ok and end_ok:
            kept.append(t)
        else:
            missing = (_("both ends") if not start_ok and not end_ok
                      else _("start") if not start_ok else _("end"))
            logger.warning(_("  track ({sx:.3f},{sy:.3f}) -> ({ex:.3f},{ey:.3f}) mm, net={net}: "
                             "{missing} does not match anything else in the selection — "
                             "probably extends beyond the intended area, skipped")
                           .format(sx=t.start.x/MM, sy=t.start.y/MM,
                                   ex=t.end.x/MM, ey=t.end.y/MM,
                                   net=t.net.name if t.net else None,
                                   missing=missing))
    return kept


def _bbox_origin(footprints: list[FootprintInstance], vias: list[Via]) -> Vector2:
    """(min_x, max_y) — lower‑left corner of the selection bounding box."""
    xs = [fp.position.x for fp in footprints] + [v.position.x for v in vias]
    ys = [fp.position.y for fp in footprints] + [v.position.y for v in vias]
    return Vector2.from_xy(min(xs), max(ys))


def _find_origin(footprints: list[FootprintInstance], vias: list[Via],
                 origin_via_net: str | None, origin_component_role: str | None,
                 origin_component_pad: str | None,
                 adapter: KiCadBoardAdapter) -> Vector2:
    """
    Default origin is bbox (see _bbox_origin). If origin_via_net or
    origin_component_role is set, origin is taken from the specific element
    in the selection (its current position on the board) rather than the bbox.
    origin_via_net and origin_component_role are mutually exclusive (checked in
    kicadstamp_cli.py). origin_component_pad is ONLY a refinement of
    origin_component_role (without it it is meaningless — fatal in CLI):
    without it origin is the component centre, with it the position of the
    specific pad (same principle as anchor_pad in ClonePlacement).
    Fatal if the element is not found or (for via_net) ambiguous — no guessing.
    """
    if origin_via_net is not None:
        candidates = [v for v in vias if v.net and v.net.name == origin_via_net]
        if not candidates:
            raise ValidationError(format_fatal_error(
                _("--origin-by-via-net {net!r} not found in selection").format(net=origin_via_net),
                [_("among {count} selected vias, none is on net {net!r}").format(
                    count=len(vias), net=origin_via_net)]
            ))
        if len(candidates) > 1:
            positions = [f"({v.position.x/MM:.3f}, {v.position.y/MM:.3f})" for v in candidates]
            raise ValidationError(format_fatal_error(
                _("--origin-by-via-net {net!r} is ambiguous").format(net=origin_via_net),
                [_("selection contains {count} vias on this net: {pos} — "
                   "refine the selection (keep only one such via) or use "
                   "--origin-by-component-role instead").format(
                       count=len(candidates), pos=positions)]
            ))
        return candidates[0].position

    if origin_component_role is not None:
        for fp in footprints:
            if adapter.get_field_value(fp, ROLE_FIELD_NAME) == origin_component_role:
                if origin_component_pad is None:
                    return fp.position
                pad = adapter.get_pad_by_number(fp, origin_component_pad)
                if pad is None:
                    raise ValidationError(format_fatal_error(
                        _("--origin-by-component-pad {pad!r} not found").format(pad=origin_component_pad),
                        [_("component with role {role!r} ({ref}) has no pad {pad!r} — "
                           "pad numbers are strings as in KiCad").format(
                               role=origin_component_role, ref=fp.reference_field.text.value,
                               pad=origin_component_pad)]
                    ))
                return pad.position
        raise ValidationError(format_fatal_error(
            _("--origin-by-component-role {role!r} not found in selection").format(role=origin_component_role),
            [_("among {count} selected components, none has role {role!r}").format(
                count=len(footprints), role=origin_component_role)]
        ))

    return _bbox_origin(footprints, vias)
