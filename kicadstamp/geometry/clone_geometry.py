# kicadstamp/geometry/clone_geometry.py
"""
clone_geometry.py — transforms a cell into absolute board coordinates
for ClonePlacement (TemplatePlacer), unlike spoke_layout.py:

  - origin = (origin_x_mm, origin_y_mm) DIRECTLY (no pad, no shift — it is an
    absolute point, not an offset from something).
  - net of each via is resolved via net_resolution.resolve_net()
    (params + net_overrides) — there is NO concept of rule_net (ClonePlacement,
    unlike Rule/ManualSpoke, has no single "rule net" at all).
    via.net=None is FATAL here — there is no sensible default to fall back to,
    unlike in spoke_layout.py.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from kipy.geometry import Vector2, Angle

from ..config import ClonePlacement, Cell, TemplateVia, TemplateTrack
from ..exceptions import ValidationError, format_fatal_error
from ..net_resolution import resolve_net
from ..utils.units import MM
from .spoke_layout import local_to_absolute, ResolvedVia, ResolvedTrack, ComponentLayout, SpokeLayout
from ..i18n import _


def _resolve_clone_via(origin: Vector2, via: TemplateVia, rotation_deg: float,
                       clone: ClonePlacement, mirror: bool = False) -> ResolvedVia:
    if via.net is None:
        raise ValidationError(format_fatal_error(
            _("via without net in cell {cell!r} ({name!r})").format(
                cell=clone.cell, name=clone.name),
            [_("via at (along={along}, across={across}) has no net — "
               "ClonePlacement has no default rule net (unlike ManualSpoke), "
               "so every via in a cloned cell must have a net explicitly set")
             .format(along=via.offset_along_mm, across=via.offset_across_mm)]
        ))
    pos = local_to_absolute(origin, via.offset_along_mm, via.offset_across_mm, rotation_deg)
    if mirror:
        pos = _mirror_x(origin, pos)
    return ResolvedVia(
        position=pos,
        net=resolve_net(via.net, clone.params, clone.net_overrides),
        drill_mm=via.drill_mm,
        diameter_mm=via.diameter_mm,
    )


def _resolve_clone_track(origin: Vector2, track: TemplateTrack, rotation_deg: float,
                         clone: ClonePlacement, tpl_layer: str,
                         mirror: bool = False) -> ResolvedTrack:
    if track.net is None:
        raise ValidationError(format_fatal_error(
            _("track without net in cell {cell!r} ({name!r})").format(
                cell=clone.cell, name=clone.name),
            [_("track (along={s_along},{s_across} -> {e_along},{e_across}) has no net — "
               "every track in a cloned cell must have a net, just like vias")
             .format(s_along=track.start_along_mm, s_across=track.start_across_mm,
                     e_along=track.end_along_mm, e_across=track.end_across_mm)]
        ))
    start = local_to_absolute(origin, track.start_along_mm, track.start_across_mm, rotation_deg)
    end = local_to_absolute(origin, track.end_along_mm, track.end_across_mm, rotation_deg)
    layer = track.layer or tpl_layer
    if mirror:
        start = _mirror_x(origin, start)
        end = _mirror_x(origin, end)
        layer = 'F.Cu' if layer == 'B.Cu' else 'B.Cu'
    return ResolvedTrack(
        start=start,
        end=end,
        width_mm=track.width_mm,
        net=resolve_net(track.net, clone.params, clone.net_overrides),
        layer=layer,
    )


def _mirror_x(origin: Vector2, p: Vector2) -> Vector2:
    """X‑mirror of a point relative to the vertical axis through origin."""
    return Vector2.from_xy(2 * origin.x - p.x, p.y)


def apply_clone_geometry(
    clone: ClonePlacement,
    cell: Cell,
    role_to_ref: Dict[str, str],
    anchor_position: Optional[Vector2] = None,
    mirror: bool = False,
) -> SpokeLayout:
    """
    Computes absolute positions of everything in the cell for a specific
    ClonePlacement. role_to_ref is already resolved EXTERNALLY (see
    clone_role_resolver.py).

    anchor_position — absolute anchor point (pad centre or footprint centre from
    anchor_ref/anchor_pad), resolved EXTERNALLY (the calculator goes to the
    adapter, geometry does not touch the live board). If set — origin_x/y_mm
    act as a FLAT shift from it (without rotation, exactly like shift in
    ManualSpoke); rotation_deg rotates only the cell contents. If None —
    origin_x/y_mm remain absolute board coordinates, as before.

    mirror=True — placement on the OPPOSITE side: the cell is assumed to be
    taken from front, final positions (after rotation) are X‑mirrored relative
    to the vertical axis through origin, component angles become 180°−φ
    (B.Cu convention from the decap placer). The anchor shift origin_x/y_mm is
    NOT mirrored — it is in board coordinates, like shift in ManualSpoke.
    Footprints on B.Cu are flipped by FlipManager (the executor sets the absolute
    angle AFTER the flip, so the +180° from the flip does not need to be accounted
    for here).
    """
    shift = Vector2.from_xy(int(clone.origin_x_mm * MM), int(clone.origin_y_mm * MM))
    if anchor_position is not None:
        origin = Vector2.from_xy(anchor_position.x + shift.x, anchor_position.y + shift.y)
    else:
        origin = shift
    rotation_deg = clone.rotation_deg

    def place(along_mm: float, across_mm: float) -> Vector2:
        p = local_to_absolute(origin, along_mm, across_mm, rotation_deg)
        return _mirror_x(origin, p) if mirror else p

    def comp_angle(angle_deg: float) -> float:
        phi = angle_deg + rotation_deg
        return (180.0 - phi) % 360.0 if mirror else phi

    layout = SpokeLayout(origin=origin)
    layout.vias = [_resolve_clone_via(origin, v, rotation_deg, clone, mirror) for v in cell.vias]
    layout.tracks = [_resolve_clone_track(origin, t, rotation_deg, clone, cell.layer, mirror)
                     for t in cell.tracks]

    for slot in cell.components:
        ref = role_to_ref.get(slot.role)
        if ref is None:
            continue
        layout.components.append(ComponentLayout(
            ref=ref,
            role=slot.role,
            position=place(slot.offset_along_mm, slot.offset_across_mm),
            angle_deg=comp_angle(slot.angle_deg),
            vias=[_resolve_clone_via(origin, v, rotation_deg, clone, mirror) for v in slot.vias],
            slot_layer=slot.layer,
        ))

    return layout