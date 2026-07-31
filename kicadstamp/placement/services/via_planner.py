# kicadstamp/placement/services/via_planner.py

import logging
from typing import List, Optional, Set, Tuple
from kipy.board_types import FootprintInstance, BoardLayer
from kipy.geometry import Vector2

from ...config import Config, ThermalViaArrayConfig
from ...geometry.keepout import Rect, build_keepout, find_free_point
from ...geometry.thermal_grid import compute_thermal_via_grid
from ...kicad.adapter import KiCadBoardAdapter
from ...utils.units import MM
from ...exceptions import GeometryError, ComponentNotFoundError, ValidationError
from ..commands import ViaCommand, PlacedComponentInfo, make_registry_key
from .clone_role_resolver import resolve_footprint_by_role
from .component_resolver import resolve_footprint_by_ref
from ...i18n import _

logger = logging.getLogger(__name__)


def thermal_anchor_id(tva: ThermalViaArrayConfig) -> str:
    """Registry identity for thermal vias — single point shared with
    kicadstamp_cli.py (known_anchor_ids), so the two never drift apart."""
    return f"thermal:{tva.name}"


class ViaPlanner:
    """
    Via planner: filters existing vias (skip_existing_components), adds thermal
    vias. Supports anchor_role for thermal vias (same as rules and ClonePlacement).
    """

    _VIA_POSITION_TOLERANCE_NM = 10_000  # 0.01 mm

    def __init__(self, adapter: KiCadBoardAdapter, config: Config, sheet_names=None,
                 resolved_points=None):
        self.adapter = adapter
        self.cfg = config
        self.sheet_names = sheet_names or {}
        # name -> ResolvedPoint, for anchor_point: — see planner.py's
        # PlacementPlanner.resolved_points (owns/shares this dict).
        self.resolved_points = resolved_points if resolved_points is not None else {}

    def _via_already_exists(self, existing_vias, position: Vector2, net_name: str) -> bool:
        for via in existing_vias:
            if not via.net or via.net.name != net_name:
                continue
            if (abs(via.position.x - position.x) <= self._VIA_POSITION_TOLERANCE_NM and
                    abs(via.position.y - position.y) <= self._VIA_POSITION_TOLERANCE_NM):
                return True
        return False

    def _resolve_thermal_anchor(self) -> Optional[FootprintInstance]:
        """
        Resolves the anchor for thermal vias by anchor_ref or anchor_role
        (with anchor_sheet and anchor_cluster). Returns a FootprintInstance or
        None if thermal vias are disabled.
        """
        tva = self.cfg.thermal_via_array
        if tva.retired:
            return None

        if tva.anchor_ref is not None:
            return resolve_footprint_by_ref(self.adapter, tva.anchor_ref, _("thermal_via_array"))

        if tva.anchor_role is not None:
            return resolve_footprint_by_role(
                self.adapter,
                tva.anchor_role,
                tva.anchor_sheet,
                tva.anchor_cluster,
                self.sheet_names,
                label="thermal_via_array"
            )

        raise ValidationError(
            _("thermal_via_array: neither anchor_ref nor anchor_role set")
        )

    def plan_vias(
        self,
        planned_components: List[PlacedComponentInfo],
        planned_vias: List[ViaCommand],
        target_fp: Optional[FootprintInstance] = None,
        target_layer: BoardLayer = BoardLayer.BL_F_Cu
    ) -> List[ViaCommand]:
        """
        Filters planned_vias through skip_existing_components, then adds
        thermal vias. If target_fp is not passed (or None), the anchor is
        resolved from cfg.thermal_via_array (supports anchor_role).
        """
        existing_vias = self.adapter.get_vias() if self.cfg.skip_existing_components else []

        # --- filter existing vias ---
        vias: List[ViaCommand] = []
        skipped = 0
        for via in planned_vias:
            if self.cfg.skip_existing_components and self._via_already_exists(
                    existing_vias, via.position, via.net_name):
                skipped += 1
                logger.debug(_("  via for {owner}: already exists, skipped")
                             .format(owner=via.owner_ref))
                continue
            vias.append(via)
        if skipped:
            logger.info(_("Skipped {count} spoke/component vias already present on the board")
                        .format(count=skipped))

        # --- thermal vias ---
        if target_fp is None:
            # resolve ourselves
            target_fp = self._resolve_thermal_anchor()

        if target_fp is not None:
            keepout = self._build_keepout(target_fp, planned_components, planned_vias=vias)
            logger.debug(_("Keepout for thermal vias: {count} rectangles").format(count=len(keepout)))
            vias.extend(self._plan_thermal_vias(
                planned_components, target_fp, keepout, existing_vias, planned_vias=vias))
        else:
            logger.debug(_("Thermal vias disabled or no anchor set — keepout/thermal planning skipped"))

        logger.info(_("plan_vias completed: {count} vias").format(count=len(vias)))
        return vias

    def _build_keepout(
        self,
        target_fp: FootprintInstance,
        planned: List[PlacedComponentInfo],
        exclude: Optional[Set[Tuple[str, str]]] = None,
        planned_vias: Optional[List[ViaCommand]] = None,
    ) -> List[Rect]:
        pad_items = []
        target_ref_name = target_fp.reference_field.text.value
        for pad in self.adapter.get_footprint_pads(target_fp):
            if exclude and (target_ref_name, pad.number) in exclude:
                continue
            pad_items.append(pad)
        for info in planned:
            fp = self.adapter.get_footprint(info.ref)
            if fp is None:
                continue
            for pad in self.adapter.get_footprint_pads(fp):
                if exclude and (info.ref, pad.number) in exclude:
                    continue
                pad_items.append(pad)
        bboxes = self.adapter.get_bounding_boxes(pad_items)
        keepout = build_keepout(bboxes, self.cfg.via_keepout_clearance_mm, mm_per_unit=MM)
        # Vias planned earlier in the same run must be treated as obstacles too,
        # otherwise a thermal via can land on top of a sibling regular via
        # (found 2026-07-31). Same clearance convention as pad keepout.
        for via in planned_vias or []:
            keepout.append(Rect.from_circle(
                via.position,
                via.diameter_mm / 2.0 * MM + int(self.cfg.via_keepout_clearance_mm * MM),
            ))
        return keepout

    def _plan_thermal_vias(
        self,
        planned: List[PlacedComponentInfo],
        target_fp: FootprintInstance,
        keepout: List[Rect],
        existing_vias: Optional[List] = None,
        planned_vias: Optional[List[ViaCommand]] = None,
    ) -> List[ViaCommand]:
        existing_vias = existing_vias or []
        tva = self.cfg.thermal_via_array
        if tva.retired:
            return []

        # target_fp should already be resolved (passed from plan_vias)
        logger.debug(_("Planning thermal vias for {ref}, pad {pad}")
                     .format(ref=target_fp.reference_field.text.value, pad=tva.pad))
        pad = self.adapter.get_pad_by_number(target_fp, tva.pad)
        if pad is None:
            raise ComponentNotFoundError(
                _("Thermal pad: {ref} has no pad {pad}").format(
                    ref=target_fp.reference_field.text.value, pad=tva.pad)
            )

        try:
            points = compute_thermal_via_grid(
                pad,
                rows=tva.rows,
                cols=tva.cols,
                margin_mm=tva.margin_mm,
                stagger=(tva.pattern == "staggered")
            )
        except GeometryError as e:
            raise GeometryError(_("Thermal pad: {error}").format(error=e))

        exclude = {(target_fp.reference_field.text.value, tva.pad)}
        keepout_excl = self._build_keepout(target_fp, planned, exclude=exclude, planned_vias=planned_vias)
        via_radius = tva.diameter_mm / 2.0 * MM
        # anchor_id/index give thermal vias a real registry_key (found 2026-07-28:
        # they never had one, so PlacementRegistry.reconcile() always treated them
        # as "to_create" — every apply run stacked a fresh set of vias on top of
        # the previous run's, 384 duplicates accumulated at 16 grid points before
        # this was caught). Index is the position in the IDEAL grid (points), not
        # in the filtered result list, so it stays stable across runs even when a
        # point is skipped (keepout/already-exists) — same convention as
        # component-slot vias (see clone_position_calculator.py).
        anchor_id = thermal_anchor_id(tva)
        result = []
        skipped = 0
        for index, p in enumerate(points):
            if self.cfg.skip_existing_components and self._via_already_exists(existing_vias, p, tva.net):
                skipped += 1
                continue
            free_p = find_free_point(
                p, keepout_excl, via_radius,
                preferred_direction=None,
                step_mm=self.cfg.via_search_step_mm,
                max_radius_mm=self.cfg.via_search_max_radius_mm,
                n_directions=self.cfg.via_search_n_directions,
            )
            if free_p is None:
                logger.warning(_("Thermal via: no free spot found for ({x:.3f}, {y:.3f}) mm, point skipped")
                               .format(x=p.x/MM, y=p.y/MM))
                continue
            result.append(ViaCommand(free_p, tva.drill_mm, tva.diameter_mm, tva.net,
                                     target_fp.reference_field.text.value,
                                     registry_key=make_registry_key(anchor_id, "thermal_via_array", None, index)))
        if skipped:
            logger.info(_("Skipped {count} thermal vias already present on the board").format(count=skipped))
        logger.info(_("Planned {count} thermal vias on {pad}").format(count=len(result), pad=tva.pad))
        return result