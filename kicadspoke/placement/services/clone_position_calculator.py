# kicadspoke/placement/services/clone_position_calculator.py
"""
clone_position_calculator.py — counterpart to manual_position_calculator.py, but
for ClonePlacement (TemplatePlacer). It does not implement IPositionCalculator
because that interface is designed specifically for the pad‑anchored ManualSpoke
model (target_fp/rules/side); ClonePlacement works fundamentally differently.

anchor_id for the registry (see registry.py) is built from PHYSICAL binding
(anchor_ref/anchor_pad, PLUS the origin_x_mm/origin_y_mm offset — see
clone_anchor_id), not from clone.name — for the same reason that refdes is not
a reliable key: the name is arbitrary and can change (renaming a
clone_placement should not erase vias/tracks if the physical anchor and offset
remain the same). Only if anchor_ref is not set at all (absolute coordinate
mode, a rare case) — we have no choice but to use clone.name, the only
available identifier.
"""
import logging
from typing import List, Tuple, Optional
from kipy.geometry import Vector2
from kipy.board_types import BoardLayer

from ...config import Config, ClonePlacement, SpokeTemplate, TemplateComponentSlot
from ...exceptions import ValidationError, format_fatal_error
from ...kicad.adapter import KiCadBoardAdapter
from ...geometry.clone_geometry import apply_clone_geometry
from ...registry import make_registry_key
from ..commands import PlacedComponentInfo, ViaCommand, TrackCommand
from .clone_role_resolver import (
    resolve_roles_by_selection,
    resolve_roles_by_nets,
    clone_uses_selection_mode,
    resolve_anchor_by_role,
)
from ...i18n import _

logger = logging.getLogger(__name__)


def clone_anchor_id(clone: ClonePlacement) -> str:
    """
    Identity of clone_placement for the registry — physical binding, not name.
    Priority order matches anchor resolution order:
      anchor_ref set -> "anchor:{ref}:{pad}:{origin_x}:{origin_y}"
      anchor_role set -> "role:{anchor_role}:{anchor_sheet}:{pad}:{origin_x}:{origin_y}"
        (anchor_role is also resilient to renaming/re‑annotation, like anchor_ref
        to clone.name changes; anchor_sheet is included because it is part of the
        anchor search conditions — changing anchor_sheet also changes the physical
        placement)
      neither (absolute coordinates) -> "name:{clone.name}",
        the only available identifier in this mode.

    origin_x_mm/origin_y_mm are included in the anchor_ref/anchor_role branches
    (found 2026-07-27): two clone_placements can legitimately share the same
    physical anchor point and differ only by this flat offset — e.g. a positive
    and a negative power-filter instance both anchored to the same connector
    pad, mirrored to opposite sides. Without the offset in the key, both
    resolved to the identical registry identity, so enabling the second one
    made the registry think its vias/tracks had merely "moved" and dragged the
    first instance's already-placed items onto itself instead of creating
    independent ones. check_no_duplicate_clone_anchors (validation.py) uses
    the same extended key, for the same reason.
    """
    if clone.anchor_ref is not None:
        return f"anchor:{clone.anchor_ref}:{clone.anchor_pad or ''}:{clone.origin_x_mm:.4f}:{clone.origin_y_mm:.4f}"
    if clone.anchor_role is not None:
        return (f"role:{clone.anchor_role}:{clone.anchor_sheet or ''}:{clone.anchor_pad or ''}"
                f":{clone.origin_x_mm:.4f}:{clone.origin_y_mm:.4f}")
    return f"name:{clone.name}"


class ClonePositionCalculator:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config

    def _resolve_anchor(self, clone: ClonePlacement) -> Optional[Vector2]:
        """
        anchor_ref/anchor_pad OR anchor_role(+anchor_sheet)/anchor_pad ->
        absolute anchor point. None if no anchor is set (absolute coordinate mode).
        A missing/ambiguous anchor is FATAL: the anchor is explicitly set, so
        placing the section "somewhere" or silently skipping it is worse than failing.
        """
        if clone.anchor_ref is not None:
            fp = self.adapter.get_footprint(clone.anchor_ref)
            if fp is None:
                raise ValidationError(format_fatal_error(
                    _("{name}: anchor component {ref!r} not found on board")
                    .format(name=clone.name, ref=clone.anchor_ref),
                    [_("check anchor_ref in clone_placement {name!r} — "
                       "no such ref on the board (typo? component not yet in PCB?)")
                     .format(name=clone.name)]
                ))
        elif clone.anchor_role is not None:
            fp = resolve_anchor_by_role(self.adapter, clone, self.cfg.sheet_names)
        else:
            return None

        if clone.anchor_pad is None:
            logger.debug(_("  [{name}] anchor: centre of {ref} ({x:.3f}, {y:.3f}) mm")
                         .format(name=clone.name, ref=fp.reference_field.text.value,
                                 x=fp.position.x/1e6, y=fp.position.y/1e6))
            return fp.position
        pad = self.adapter.get_pad_by_number(fp, clone.anchor_pad)
        if pad is None:
            raise ValidationError(format_fatal_error(
                _("{name}: {ref} has no pad {pad!r}").format(name=clone.name,
                                                             ref=fp.reference_field.text.value,
                                                             pad=clone.anchor_pad),
                [_("check anchor_pad in clone_placement {name!r} — "
                   "pad numbers are strings as in KiCad ('1', '17', 'A3')")
                 .format(name=clone.name)]
            ))
        logger.debug(_("  [{name}] anchor: pad {ref}.{pad} ({x:.3f}, {y:.3f}) mm")
                     .format(name=clone.name, ref=fp.reference_field.text.value,
                             pad=clone.anchor_pad,
                             x=pad.position.x/1e6, y=pad.position.y/1e6))
        return pad.position

    def compute_raw_positions(
        self,
        clone_placements: List[ClonePlacement],
    ) -> Tuple[List[PlacedComponentInfo], List[ViaCommand], List[TrackCommand]]:
        components_result: List[PlacedComponentInfo] = []
        vias_result: List[ViaCommand] = []
        tracks_result: List[TrackCommand] = []

        for clone in clone_placements:
            if not clone.enabled:
                continue

            if clone.template is not None:
                template = self.cfg.templates.get(clone.template)
                if template is None:
                    logger.warning(_("{name}: template {template!r} not found in templates, skipping")
                                   .format(name=clone.name, template=clone.template))
                    continue
                template_name = clone.template
            else:
                # role: instead of template — single‑component placement without
                # a separate template file (see discussion: creating a template
                # entry just for one role with no via/track is cumbersome).
                # Synthesise a temporary SpokeTemplate on the fly (cheap — one
                # component, no caching needed).
                template = SpokeTemplate(
                    name=f"__role__{clone.role}",
                    components=[TemplateComponentSlot(
                        role=clone.role, offset_along_mm=0.0, offset_across_mm=0.0, angle_deg=0.0,
                    )],
                )
                template_name = template.name

            # Resolve anchor BEFORE role resolution — needed for physical
            # proximity narrowing (resolve_roles_by_nets), and the same anchor
            # is later used in apply_clone_geometry (avoid double resolution).
            anchor_position = self._resolve_anchor(clone)

            # Mode: "by nets" if nets OR params are set — otherwise "by selection".
            # Explicit decision outside, not automatic inside the resolver
            # (see clone_role_resolver.py).
            if clone_uses_selection_mode(clone):
                role_to_ref = resolve_roles_by_selection(self.adapter, template, clone,
                                                         anchor_position=anchor_position)
            else:
                role_to_ref = resolve_roles_by_nets(self.adapter, template, clone,
                                                    anchor_position=anchor_position)

            # Placement side: own layer of the clone or global from config.
            # mirror — explicit manual operation; correctness of layer/mirror pair
            # is already fatal‑checked in load_config.
            mirror = clone.mirror
            # Template is assumed to be front; back = mirror (see apply_clone_geometry)
            layout = apply_clone_geometry(clone, template, role_to_ref,
                                          anchor_position=anchor_position,
                                          mirror=mirror)
            logger.info(_("  [{name}] template {tpl!r} on {layer}{mirror_suffix}")
                        .format(name=clone.name, tpl=template.name, layer=template.layer,
                                mirror_suffix=_(" -> mirrored as a whole") if mirror else _(" -> as written")))
            anchor_id = clone_anchor_id(clone)

            for via_index, via in enumerate(layout.vias):
                vias_result.append(ViaCommand(
                    position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                    net_name=via.net, owner_ref=clone.name,
                    registry_key=make_registry_key(anchor_id, template_name, None, via_index),
                ))
                logger.debug(_("  [{name}] spoke‑level via: ({x:.3f}, {y:.3f}) mm, net={net}")
                             .format(name=clone.name, x=via.position.x/1e6,
                                     y=via.position.y/1e6, net=via.net))

            for track_index, track in enumerate(layout.tracks):
                track_layer = BoardLayer.BL_B_Cu if track.layer == 'B.Cu' else BoardLayer.BL_F_Cu
                tracks_result.append(TrackCommand(
                    start=track.start, end=track.end, width_mm=track.width_mm,
                    net_name=track.net, layer=track_layer, owner_ref=clone.name,
                    registry_key=make_registry_key(anchor_id, template_name, None, track_index),
                ))
                logger.debug(_("  [{name}] track: ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, "
                               "net={net}, layer={layer}")
                             .format(name=clone.name, sx=track.start.x/1e6, sy=track.start.y/1e6,
                                     ex=track.end.x/1e6, ey=track.end.y/1e6,
                                     net=track.net, layer=track.layer))

            for comp_layout in layout.components:
                # Slot layer: its own absolute or inherited from template;
                # mirror inverts ALL layers — the construction is flipped as a
                # physical object.
                slot_layer = comp_layout.slot_layer or template.layer
                if mirror:
                    slot_layer = 'F.Cu' if slot_layer == 'B.Cu' else 'B.Cu'
                comp_layer = BoardLayer.BL_B_Cu if slot_layer == 'B.Cu' else BoardLayer.BL_F_Cu
                components_result.append(PlacedComponentInfo(
                    ref=comp_layout.ref, dest=comp_layout.position, angle_deg=comp_layout.angle_deg,
                    layer=comp_layer,
                ))
                logger.debug(
                    _("  [{name}] {ref} (role {role}): position ({x:.3f}, {y:.3f}) mm, angle {angle:.1f}°")
                    .format(name=clone.name, ref=comp_layout.ref, role=comp_layout.role,
                            x=comp_layout.position.x/1e6, y=comp_layout.position.y/1e6,
                            angle=comp_layout.angle_deg)
                )
                for via_index, via in enumerate(comp_layout.vias):
                    vias_result.append(ViaCommand(
                        position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                        net_name=via.net, owner_ref=comp_layout.ref,
                        registry_key=make_registry_key(anchor_id, template_name, comp_layout.role, via_index),
                    ))

        return components_result, vias_result, tracks_result