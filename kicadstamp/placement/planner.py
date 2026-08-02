# kicadstamp/placement/planner.py

import logging


from kipy.board_types import BoardLayer
from kipy.geometry import Angle, Vector2

from ..config import Config
from ..kicad.adapter import KiCadBoardAdapter
from ..utils.units import MM
from .services.via_planner import ViaPlanner
from .services.manual_position_calculator import ManualPositionCalculator
from .services.clone_position_calculator import ClonePositionCalculator
from .services.point_resolver import resolve_point, ResolvedPoint
from .services.position_tracker import PositionTracker
from ..exceptions import ComponentNotFoundError, ValidationError
from .commands import MoveCommand, ViaCommand, TrackCommand, PlacedComponentInfo
from ..i18n import _

logger = logging.getLogger(__name__)


class PlacementPlanner:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, sheet_names=None):
        _sn = sheet_names or {}
        self.adapter = adapter
        self.cfg = config
        self.sheet_names = _sn
        # Populated as Point items are planned (see plan_item's kind == 'point'
        # branch) — a name -> ResolvedPoint cache, consulted by anchor_point:
        # on Rule/ClonePlacement/ThermalViaArrayConfig. Passed BY REFERENCE
        # into the calculators below so they see it live-updated as the run
        # progresses, same freshness discipline as everything else here.
        self.resolved_points: dict[str, ResolvedPoint] = {}
        self.position_calc = ManualPositionCalculator(adapter, config, sheet_names=_sn,
                                                       resolved_points=self.resolved_points)
        self.clone_calc = ClonePositionCalculator(adapter, config, sheet_names=_sn,
                                                   resolved_points=self.resolved_points)
        # No global target_fp any more: anchors are per‑rule (Rule.anchor_ref / anchor_role)
        # and per‑tva; resolved on the spot.
        self._target_layer = BoardLayer.BL_B_Cu if config.layer == 'B.Cu' else BoardLayer.BL_F_Cu
        self._tracker = PositionTracker(adapter, self._target_layer,
                                         skip_existing=config.skip_existing_components)
        self._planned = None
        self._planned_vias = None
        self._planned_tracks = None
        self.via_planner = ViaPlanner(adapter, config, sheet_names=_sn,
                                      resolved_points=self.resolved_points)
        logger.info(_("Planner initialised: layer={layer}, anchors in rules: {anchors}")
                    .format(layer=config.layer,
                            anchors=len({r.anchor_ref or r.anchor_role for r in config.rules})))

    def begin_planning(self) -> None:
        """Resets the accumulated plan — call once before either plan_moves()
        (single-snapshot, dry-run) or a series of plan_item() calls (real
        apply, one per dependency_order.Item — see cmd_apply)."""
        self._planned = []
        self._planned_vias = []
        self._planned_tracks = []

    def plan_item(self, item) -> list[MoveCommand]:
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
        if item.kind == 'point':
            # No board mutation — just resolve the point and cache it for
            # whoever references it via anchor_point. plan_items()/execute's
            # per-item loop already treats an empty move list as a no-op, so
            # nothing else in ApplyPipeline needs to know this Item.kind exists.
            resolved = resolve_point(self.adapter, item.obj, self.resolved_points,
                                     sheet_names=self.sheet_names)
            self.resolved_points[item.obj.name] = resolved
            return []
        if item.kind == 'rule':
            placed, vias, tracks = self.position_calc.compute_raw_positions([item.obj])
        else:
            placed, vias, tracks = self.clone_calc.compute_raw_positions([item.obj])
        self._planned.extend(placed)
        self._planned_vias.extend(vias)
        self._planned_tracks.extend(tracks)
        return self._tracker.moves_from_placed(placed)

    def moves_from_placed(self, placed: list) -> list[MoveCommand]:
        """Convenience wrapper — delegates to PositionTracker."""
        return self._tracker.moves_from_placed(placed)

    def plan_items(self, items) -> list[MoveCommand]:
        """Convenience for dry-run: plan_item() for every item in dependency
        order, from a single unchanged board snapshot (no execution/refresh
        between items — see the caveat cmd_apply prints in --dry-run output),
        returning the combined move list. Real apply uses plan_item() directly,
        one at a time, interleaved with execution and adapter.refresh_board()."""
        self.begin_planning()
        moves: list[MoveCommand] = []
        for item in items:
            moves.extend(self.plan_item(item))
        return moves

    def plan_moves(self) -> list[MoveCommand]:
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
        # cell.layer/slot.layer/mirror); None — only for ManualSpoke path
        # (manual_position_calculator.py does not set it), then inherit the global
        # target_layer from config.
        moves = self._tracker.moves_from_placed(self._planned)
        logger.info(_("plan_moves completed: {count} moves").format(count=len(moves)))
        return moves

    def plan_vias(self) -> list[ViaCommand]:
        # Thermal via anchor resolution (anchor_ref/anchor_role/anchor_sheet/
        # anchor_cluster), one per cfg.thermal_via_arrays entry, is entirely
        # inside via_planner.py (_resolve_thermal_anchor) — we do not
        # duplicate that logic here.
        return self.via_planner.plan_vias(
            planned_components=self._planned,
            planned_vias=self._planned_vias,
            target_layer=self._target_layer
        )

    def plan_tracks(self) -> list[TrackCommand]:
        """
        Tracks are planned for both ClonePlacement and ManualSpoke (net=None in
        TemplateTrack inherits rule.net — see spoke_layout._resolve_track).
        No keepout/thermal planning here, unlike plan_vias — tracks are not
        moved to avoid collisions; collision checking for long geometry is
        deliberately not done (see discussion), we rely on KiCad DRC after
        placement.
        """
        return list(self._planned_tracks or [])

    def plan(self) -> tuple[list[MoveCommand], list[ViaCommand], list[TrackCommand]]:
        moves = self.plan_moves()
        vias = self.plan_vias()
        tracks = self.plan_tracks()
        return moves, vias, tracks
