# kicadspoke/placement/services/manual_position_calculator.py

import logging
from typing import List, Optional, Set, Tuple
from kipy.board_types import FootprintInstance, BoardLayer

from ...config import Config, Rule
from ...kicad.adapter import KiCadBoardAdapter
from ...geometry.spoke_layout import apply_spoke_geometry
from ..commands import PlacedComponentInfo, ViaCommand, TrackCommand
from ...registry import make_registry_key
from .component_pool import ComponentPool
from ...i18n import _

logger = logging.getLogger(__name__)


def resolve_rule_anchor_ref(adapter: KiCadBoardAdapter, cfg: Config, rule: Rule) -> Optional[str]:
    """
    Resolves rule's anchor to a concrete ref — see resolve_clone_anchor_ref in
    clone_position_calculator.py, same rationale (used by dependency_order.py
    to build the producer/consumer graph before any planning happens).
    """
    if rule.anchor_ref is not None:
        return rule.anchor_ref
    if rule.anchor_role is not None:
        from .clone_role_resolver import resolve_footprint_by_role
        fp = resolve_footprint_by_role(
            adapter, rule.anchor_role, rule.anchor_sheet, rule.anchor_cluster,
            cfg.sheet_names, label=_("rule (net {net!r})").format(net=rule.net),
        )
        return fp.reference_field.text.value
    return None


def rule_anchor_ids(rule: Rule) -> Set[str]:
    """
    Registry identity/identities of a rule — one 'pad:{pad}' per ENABLED
    spoke (see compute_raw_positions below: anchor_id = f"pad:{spoke.pad}",
    passed to make_registry_key). Unlike ClonePlacement (one clone_anchor_id
    per placement), a Rule is a GROUP of per-pad spokes, each with its own
    registry identity — so this returns a set, not a single id.

    Used for known_anchor_ids (kicadspoke_cli.py's cmd_apply, see
    clone_anchor_id/thermal_anchor_id for the same idea). Without this, a
    rule excluded from a run (enabled: false, --only, --cluster) has its
    via/track registry entries pruned unconditionally —
    registry.reconcile()'s known_anchor_ids protection only recognises the
    'anchor:'/'role:'/'name:'/'thermal:' prefixes (ClonePlacement/
    thermal_via_array), never 'pad:', so rule-based geometry was never
    actually protected by --only/--cluster at all (found 2026-07-29: "hiding
    part of fpga.yaml's rules deletes its routing", true even without
    touching enabled at all — just being excluded by --only was enough).
    """
    return {f"pad:{spoke.pad}" for spoke in rule.spokes if spoke.enabled}


class ManualPositionCalculator:
    """
    Manual positioning of components and vias via spoke templates.
    Supports clusters: for each unique cluster in the rule, a separate
    ComponentPool is built, and spokes take components from their own cluster.
    """

    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config

    def compute_raw_positions(
        self,
        rules: List[Rule],
    ) -> Tuple[List[PlacedComponentInfo], List[ViaCommand], List[TrackCommand]]:
        components_result: List[PlacedComponentInfo] = []
        vias_result: List[ViaCommand] = []
        tracks_result: List[TrackCommand] = []

        for rule in rules:
            # --- Resolve anchor (anchor_ref or anchor_role) ---
            if rule.anchor_ref is not None:
                target_fp = self.adapter.get_footprint(rule.anchor_ref)
                if target_fp is None:
                    from ...exceptions import ComponentNotFoundError
                    raise ComponentNotFoundError(
                        _("rule (net {net!r}): anchor {anchor!r} not found on board")
                        .format(net=rule.net, anchor=rule.anchor_ref)
                    )
            else:
                from .clone_role_resolver import resolve_footprint_by_role
                target_fp = resolve_footprint_by_role(
                    self.adapter,
                    rule.anchor_role,
                    rule.anchor_sheet,
                    rule.anchor_cluster,
                    self.cfg.sheet_names,
                    label=_("rule (net {net!r})").format(net=rule.net),
                )
            anchor_ref_resolved = target_fp.reference_field.text.value

            # --- Collect all roles needed for this rule ---
            roles_needed = set()
            for spoke in rule.spokes:
                if not spoke.enabled:
                    continue
                template = self.cfg.templates.get(spoke.template)
                if template is not None:
                    roles_needed.update(slot.role for slot in template.components)

            # Important: do not skip the rule entirely if roles_needed is empty —
            # this only means "no component‑bearing slots in any spoke template",
            # not "the rule has no spokes at all". Spokes can carry spoke‑level
            # vias without any sub‑components (e.g. cap_pair_standard without
            # components in old configs) — they don't need a pool at all, but we
            # still need to create their geometry/vias. An empty roles_needed just
            # gives empty pools below — cheap, no special branch needed.

            # --- Collect clusters used in spokes (including None) ---
            clusters_needed = {spoke.cluster for spoke in rule.spokes if spoke.enabled}

            # --- Build pools for each cluster, including None (spokes without
            # cluster) — clusters_needed already contains None if any enabled
            # spoke has no cluster, so no separate pass needed ---
            pools_by_cluster = {}
            for cluster in clusters_needed:
                pools_by_cluster[cluster] = ComponentPool(
                    self.adapter,
                    rule.net,
                    roles=sorted(roles_needed),
                    cluster=cluster
                )

            # --- Process each spoke ---
            for spoke in rule.spokes:
                if not spoke.enabled:
                    continue

                template = self.cfg.templates.get(spoke.template)
                if template is None:
                    logger.warning(
                        _("Spoke on pad {pad}: template {template!r} not found in templates, spoke skipped")
                        .format(pad=spoke.pad, template=spoke.template)
                    )
                    continue

                pad = self.adapter.get_pad_by_number(target_fp, spoke.pad)
                if pad is None:
                    logger.warning(
                        _("{anchor} has no pad {pad}, spoke skipped")
                        .format(anchor=anchor_ref_resolved, pad=spoke.pad)
                    )
                    continue

                # Select pool by spoke cluster — by construction pools_by_cluster
                # already contains the key spoke.cluster (see clusters_needed above)
                # for any enabled spoke; if it ever stops being true, let it fail
                # loudly (KeyError) rather than silently substituting a freshly
                # created pool that bypasses shared consumption accounting.
                pool = pools_by_cluster[spoke.cluster]

                # Consume pool by roles
                role_to_ref = {slot.role: pool.pop(slot.role, spoke.pad) for slot in template.components}

                layout = apply_spoke_geometry(pad.position, spoke, template, rule.net, role_to_ref)
                anchor_id = f"pad:{spoke.pad}"

                # Spoke‑level vias
                for via_index, via in enumerate(layout.vias):
                    vias_result.append(ViaCommand(
                        position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                        net_name=via.net, owner_ref=anchor_ref_resolved,
                        registry_key=make_registry_key(anchor_id, spoke.template, None, via_index),
                    ))
                    logger.debug(
                        _("  spoke‑level via (pad {pad}): ({x:.3f}, {y:.3f}) mm, net={net}")
                        .format(pad=spoke.pad, x=via.position.x/1e6, y=via.position.y/1e6, net=via.net)
                    )

                # Spoke‑level tracks (net=None in template inherits rule.net —
                # see spoke_layout._resolve_track). Only spoke‑level: TemplateComponentSlot
                # carries vias, not tracks.
                for track_index, track in enumerate(layout.tracks):
                    track_layer = BoardLayer.BL_B_Cu if track.layer == 'B.Cu' else BoardLayer.BL_F_Cu
                    tracks_result.append(TrackCommand(
                        start=track.start, end=track.end, width_mm=track.width_mm,
                        net_name=track.net, layer=track_layer, owner_ref=anchor_ref_resolved,
                        registry_key=make_registry_key(anchor_id, spoke.template, None, track_index),
                    ))
                    logger.debug(
                        _("  spoke‑level track (pad {pad}): ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, net={net}, layer={layer}")
                        .format(pad=spoke.pad, sx=track.start.x/1e6, sy=track.start.y/1e6,
                                ex=track.end.x/1e6, ey=track.end.y/1e6, net=track.net, layer=track.layer)
                    )

                # Component‑level slots. Slot layer: its own absolute or
                # inherited from the template — same convention as
                # ClonePlacement (clone_position_calculator.py), minus
                # mirror (ManualSpoke does not support it, see
                # spoke_layout._resolve_track's docstring). Previously
                # never set here at all — components silently inherited
                # PlacementPlanner's single global target_layer regardless
                # of what the template itself declared (found live:
                # templates/fpga_cap_pair_spoke.yaml's layer: B.Cu was
                # honoured for its tracks but not its components).
                for comp_layout in layout.components:
                    slot_layer = comp_layout.slot_layer or template.layer
                    comp_layer = BoardLayer.BL_B_Cu if slot_layer == 'B.Cu' else BoardLayer.BL_F_Cu
                    components_result.append(PlacedComponentInfo(
                        ref=comp_layout.ref, dest=comp_layout.position, angle_deg=comp_layout.angle_deg,
                        layer=comp_layer,
                    ))
                    logger.debug(
                        _("  {ref} (role {role}, pad {pad}): position ({x:.3f}, {y:.3f}) mm, angle {angle:.1f}°")
                        .format(ref=comp_layout.ref, role=comp_layout.role, pad=spoke.pad,
                                x=comp_layout.position.x/1e6, y=comp_layout.position.y/1e6,
                                angle=comp_layout.angle_deg)
                    )
                    for via_index, via in enumerate(comp_layout.vias):
                        vias_result.append(ViaCommand(
                            position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                            net_name=via.net, owner_ref=comp_layout.ref,
                            registry_key=make_registry_key(anchor_id, spoke.template, comp_layout.role, via_index),
                        ))
                        logger.debug(
                            _("    via {ref}: ({x:.3f}, {y:.3f}) mm, net={net}")
                            .format(ref=comp_layout.ref, x=via.position.x/1e6, y=via.position.y/1e6, net=via.net)
                        )

        return components_result, vias_result, tracks_result