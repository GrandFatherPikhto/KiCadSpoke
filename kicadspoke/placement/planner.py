# kicadspoke/placement/planner.py

import logging
from typing import List, Tuple, Optional
from kipy.board_types import BoardLayer, Pad, FootprintInstance
from kipy.geometry import Vector2, Angle

from ..config import Config
from ..kicad.adapter import KiCadBoardAdapter
from ..utils.units import MM
from .services.via_planner import ViaPlanner
from .services.manual_position_calculator import ManualPositionCalculator
from .services.clone_position_calculator import ClonePositionCalculator
from ..exceptions import ComponentNotFoundError, ValidationError
from .commands import MoveCommand, ViaCommand, TrackCommand
from ..constants import POSITION_TOLERANCE_NM, ANGLE_TOLERANCE_DEG
from ..i18n import _

logger = logging.getLogger(__name__)


class PlacementPlanner:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, sheet_names=None):
        _sn = sheet_names or {}
        self.adapter = adapter
        self.cfg = config
        self.position_calc = ManualPositionCalculator(adapter, config, sheet_names=_sn)
        self.clone_calc = ClonePositionCalculator(adapter, config, sheet_names=_sn)
        # No global target_fp any more: anchors are per‑rule (Rule.anchor_ref / anchor_role)
        # and per‑tva; resolved on the spot.
        self._target_layer = BoardLayer.BL_B_Cu if config.layer == 'B.Cu' else BoardLayer.BL_F_Cu
        self._planned = None
        self._planned_vias = None
        self._planned_tracks = None
        self.via_planner = ViaPlanner(adapter, config, sheet_names=_sn)
        logger.info(_("Planner initialised: layer={layer}, anchors in rules: {anchors}")
                    .format(layer=config.layer,
                            anchors=len({r.anchor_ref or r.anchor_role for r in config.rules})))

    # Tolerances for "already in place" checks (skip_existing_components) —
    # coarse enough to ignore rounding noise from IPC, but tight enough not to
    # confuse with a truly different target position.
    _POSITION_TOLERANCE_NM = POSITION_TOLERANCE_NM
    _ANGLE_TOLERANCE_DEG = ANGLE_TOLERANCE_DEG

    def _already_in_place(self, ref: str, dest: Vector2, angle_deg: float, layer: BoardLayer) -> bool:
        fp = self.adapter.get_footprint(ref)
        if fp is None:
            return False
        if fp.layer != layer:
            return False
        if abs(fp.position.x - dest.x) > self._POSITION_TOLERANCE_NM:
            return False
        if abs(fp.position.y - dest.y) > self._POSITION_TOLERANCE_NM:
            return False
        angle_diff = abs((fp.orientation.degrees - angle_deg + 180) % 360 - 180)
        return angle_diff <= self._ANGLE_TOLERANCE_DEG

    def begin_planning(self) -> None:
        """Resets the accumulated plan — call once before either plan_moves()
        (single-snapshot, dry-run) or a series of plan_item() calls (real
        apply, one per dependency_order.Item — see cmd_apply)."""
        self._planned = []
        self._planned_vias = []
        self._planned_tracks = []

    def plan_item(self, item) -> List[MoveCommand]:
        """
        Plans ONE dependency_order.Item (rule or clone_placement) against the
        board as it is RIGHT NOW — the caller is responsible for calling
        adapter.refresh_board() beforehand if an earlier item in this same run
        may have changed it (see dependency_order.py / cmd_apply's per-item
        loop). Accumulates into self._planned/_planned_vias/_planned_tracks so
        that plan_vias()/plan_tracks(), called once after the whole loop
        finishes, return the combined result for the run exactly as before —
        via/track CREATION does not need to be interleaved between items,
        only moves do (vias/tracks are pure geometry already anchored to
        positions resolved at this point).
        """
        if item.kind == 'rule':
            placed, vias, tracks = self.position_calc.compute_raw_positions([item.obj])
        else:
            placed, vias, tracks = self.clone_calc.compute_raw_positions([item.obj])
        self._planned.extend(placed)
        self._planned_vias.extend(vias)
        self._planned_tracks.extend(tracks)
        return self.moves_from_placed(placed)

    def moves_from_placed(self, placed: List) -> List[MoveCommand]:
        moves = []
        skipped = 0
        for info in placed:
            layer = info.layer if info.layer is not None else self._target_layer
            if self.cfg.skip_existing_components and self._already_in_place(info.ref, info.dest, info.angle_deg, layer):
                skipped += 1
                logger.debug(_("  {ref}: already in place, move skipped (skip_existing_components)")
                             .format(ref=info.ref))
                continue
            moves.append(MoveCommand(
                ref=info.ref,
                position=info.dest,
                angle=Angle.from_degrees(info.angle_deg),
                layer=layer
            ))
        if skipped:
            logger.info(_("Skipped {count} components already at target position").format(count=skipped))
        return moves

    def plan_items(self, items) -> List[MoveCommand]:
        """Convenience for dry-run: plan_item() for every item in dependency
        order, from a single unchanged board snapshot (no execution/refresh
        between items — see the caveat cmd_apply prints in --dry-run output),
        returning the combined move list. Real apply uses plan_item() directly,
        one at a time, interleaved with execution and adapter.refresh_board()."""
        self.begin_planning()
        moves: List[MoveCommand] = []
        for item in items:
            moves.extend(self.plan_item(item))
        return moves

    def plan_moves(self) -> List[MoveCommand]:
        self.begin_planning()

        if self.cfg.place_components and self.cfg.rules:
            placed, planned_vias, planned_tracks = self.position_calc.compute_raw_positions(
                self.cfg.rules
            )
            self._planned.extend(placed)
            self._planned_vias.extend(planned_vias)
            self._planned_tracks.extend(planned_tracks)
        elif not self.cfg.rules:
            logger.debug(_("rules is empty — no ManualSpoke components/vias/tracks planned"))
        else:
            logger.info(_("place_components=False – component moves are not planned"))

        if self.cfg.clone_placements:
            clone_placed, clone_vias, clone_tracks = self.clone_calc.compute_raw_positions(self.cfg.clone_placements)
            self._planned.extend(clone_placed)
            self._planned_vias.extend(clone_vias)
            self._planned_tracks.extend(clone_tracks)
            logger.info(_("ClonePlacement: {count} components, {vias} vias, {tracks} tracks")
                        .format(count=len(clone_placed), vias=len(clone_vias), tracks=len(clone_tracks)))

        # info.layer — per‑component (ClonePositionCalculator already accounted for
        # template.layer/slot.layer/mirror); None — only for ManualSpoke path
        # (manual_position_calculator.py does not set it), then inherit the global
        # target_layer from config.
        moves = self.moves_from_placed(self._planned)
        logger.info(_("plan_moves completed: {count} moves").format(count=len(moves)))
        return moves

    def plan_vias(self) -> List[ViaCommand]:
        # Thermal via anchor resolution (anchor_ref/anchor_role/anchor_sheet/
        # anchor_cluster) is entirely inside via_planner.py (_resolve_thermal_anchor)
        # — we do not duplicate that logic here; target_fp=None means "resolve
        # yourself from cfg.thermal_via_array".
        return self.via_planner.plan_vias(
            planned_components=self._planned,
            planned_vias=self._planned_vias,
            target_fp=None,
            target_layer=self._target_layer
        )

    def plan_tracks(self) -> List[TrackCommand]:
        """
        Tracks are planned for both ClonePlacement and ManualSpoke (net=None in
        TemplateTrack inherits rule.net — see spoke_layout._resolve_track).
        No keepout/thermal planning here, unlike plan_vias — tracks are not
        moved to avoid collisions; collision checking for long geometry is
        deliberately not done (see discussion), we rely on KiCad DRC after
        placement.
        """
        return list(self._planned_tracks or [])

    def plan(self) -> Tuple[List[MoveCommand], List[ViaCommand], List[TrackCommand]]:
        moves = self.plan_moves()
        vias = self.plan_vias()
        tracks = self.plan_tracks()
        return moves, vias, tracks