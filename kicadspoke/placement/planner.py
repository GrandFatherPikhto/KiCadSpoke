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
from ..exceptions import ComponentNotFoundError
from .commands import MoveCommand, ViaCommand

from ..constants import POSITION_TOLERANCE_NM, ANGLE_TOLERANCE_DEG

logger = logging.getLogger(__name__)

class PlacementPlanner:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config
        self.position_calc = ManualPositionCalculator(adapter, config)
        self.clone_calc = ClonePositionCalculator(adapter, config)
        # Глобального target_fp больше нет: якоря — per-rule (Rule.anchor_ref)
        # и per-tva; резолвятся по месту использования.
        self._target_layer = BoardLayer.BL_B_Cu if config.layer == 'B.Cu' else BoardLayer.BL_F_Cu
        self._planned = None
        self._planned_vias = None
        self.via_planner = ViaPlanner(adapter, config)
        logger.info(f"Планировщик инициализирован: layer={config.layer}, "
                    f"якорей в правилах: {len({r.anchor_ref for r in config.rules})}")

    # Допуски для проверки "уже на месте" (skip_existing_components) —
    # достаточно грубые, чтобы не реагировать на шум округления при
    # повторном чтении координат из IPC, но достаточно точные, чтобы не
    # спутать с реально другой целевой позицией.
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

    def plan_moves(self) -> List[MoveCommand]:
        self._planned = []
        self._planned_vias = []

        if self.cfg.place_components and self.cfg.rules:
            placed, planned_vias = self.position_calc.compute_raw_positions(
                self.cfg.rules
            )
            self._planned.extend(placed)
            self._planned_vias.extend(planned_vias)
        elif not self.cfg.rules:
            logger.debug("rules пуст — компоненты/via по ManualSpoke не планируются")
        else:
            logger.info("place_components=False – перемещения конденсаторов не планируются")

        if self.cfg.clone_placements:
            clone_placed, clone_vias = self.clone_calc.compute_raw_positions(self.cfg.clone_placements)
            self._planned.extend(clone_placed)
            self._planned_vias.extend(clone_vias)
            logger.info(f"ClonePlacement: {len(clone_placed)} компонентов, {len(clone_vias)} via")

        moves = []
        skipped = 0
        for info in self._planned:
            # info.layer — per-компонентный (ClonePositionCalculator уже
            # учёл template.layer/slot.layer/mirror); None — только у
            # ManualSpoke-пути (manual_position_calculator.py его не
            # задаёт), тогда наследуем глобальный target_layer конфига.
            layer = info.layer if info.layer is not None else self._target_layer
            if self.cfg.skip_existing_components and self._already_in_place(info.ref, info.dest, info.angle_deg, layer):
                skipped += 1
                logger.debug(f"  {info.ref}: уже на месте, перемещение пропущено (skip_existing_components)")
                continue
            moves.append(MoveCommand(
                ref=info.ref,
                position=info.dest,
                angle=Angle.from_degrees(info.angle_deg),
                layer=layer
            ))
        if skipped:
            logger.info(f"Пропущено {skipped} компонентов, уже находящихся на целевой позиции")
        logger.info(f"plan_moves завершено: {len(moves)} перемещений")
        return moves

    def plan_vias(self) -> List[ViaCommand]:
        tva = self.cfg.thermal_via_array
        thermal_anchor_fp = None
        if tva.enabled:
            thermal_anchor_fp = self.adapter.get_footprint(tva.anchor_ref)
            if thermal_anchor_fp is None:
                raise ComponentNotFoundError(
                    f"thermal_via_array: якорь {tva.anchor_ref!r} не найден")
        return self.via_planner.plan_vias(
            planned_components=self._planned,
            planned_vias=self._planned_vias,
            target_fp=thermal_anchor_fp,
            target_layer=self._target_layer
        )

    def plan(self) -> Tuple[List[MoveCommand], List[ViaCommand]]:
        moves = self.plan_moves()
        vias = self.plan_vias()
        return moves, vias