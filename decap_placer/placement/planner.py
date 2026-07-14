# decap_placer/placement/planner.py

import math
import logging
from typing import List, Tuple, Optional, Set
from kipy.board_types import BoardLayer, Pad, FootprintInstance
from kipy.geometry import Vector2, Angle

from ..config import Config, ViaConfig, SpokeComponent, Spoke
from ..kicad.adapter import KiCadBoardAdapter
from ..geometry.strategies import PlacementStrategy, RadialStrategy, OrthogonalStrategy, FixedStrategy, BoundaryStrategy
from ..geometry.boundary import polyline_points
from ..geometry.thermal_grid import compute_thermal_via_grid
from ..geometry.relax import relax_positions
from ..geometry.keepout import Rect, build_keepout, find_free_point
from ..utils.units import MM

from .services.position_calculator import PositionCalculator
from .services.power_pin_orienter import PowerPinOrienter
from .services.spacing_relaxer import SpacingRelaxer
# from .services.keepout_builder import KeepoutBuilder
from .services.via_planner import ViaPlanner

from ..optimization.factory import OptimizerFactory
from ..optimization.heuristic_optimizer import HeuristicOptimizer

from ..exceptions import ComponentNotFoundError, GeometryError

from dataclasses import dataclass

from .commands import MoveCommand, ViaCommand

logger = logging.getLogger(__name__)


class PlacementPlanner:

    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config
        self.optimizer = OptimizerFactory.create(config.optimizer_type, adapter, config)
        # self.spacing_relaxer = SpacingRelaxer(adapter, config)
        self._strategy = self._create_strategy()
        self._target_fp = adapter.get_footprint(config.target_ref)
        if self._target_fp is None:
            raise ComponentNotFoundError(f"Целевой компонент {config.target_ref} не найден")
        # self.position_calc = PositionCalculator(adapter, config)
        # self.power_pin_orienter = PowerPinOrienter(adapter, config)
        # self.keepout_builder = KeepoutBuilder(adapter, config)
        # self.via_planner = ViaPlanner(adapter, config, self.keepout_builder)
        self.via_planner = ViaPlanner(adapter, config)
        self._center = self._target_fp.position
        self._target_layer = BoardLayer.BL_B_Cu if config.side == "back" else BoardLayer.BL_F_Cu
        self._boundary_polygon = self._get_boundary_polygon()
        self._zone_center_point = self._compute_zone_center(self._boundary_polygon)
        self._planned = None  # заполняется в plan_moves(), нужно plan_vias()
        logger.info(f"Планировщик инициализирован: target={config.target_ref}, side={config.side}")

    def _create_strategy(self) -> PlacementStrategy:
        mode = self.cfg.rotation_mode
        if mode == "radial":
            logger.debug("Выбрана радиальная стратегия")
            return RadialStrategy()
        elif mode == "orthogonal":
            logger.debug("Выбрана ортогональная стратегия")
            return OrthogonalStrategy()
        elif mode == "fixed":
            logger.debug(f"Выбрана фиксированная стратегия (угол {self.cfg.fixed_angle_deg}°)")
            return FixedStrategy()
        elif mode == "boundary":
            logger.debug(f"Выбрана стратегия по границам")
            return BoundaryStrategy()
        else:
            raise ValueError(f"Неизвестный rotation_mode: {mode}")

    def _get_boundary_polygon(self):
        zone = self.adapter.get_zone_by_name(self.cfg.boundary_zone)
        if zone is None:
            raise ComponentNotFoundError(f"Зона {self.cfg.boundary_zone} не найдена")
        pts = polyline_points(zone.outline.outline)
        logger.debug(f"Граница зоны содержит {len(pts)} точек")
        return pts

    def _compute_zone_center(self, boundary_polygon: List[Vector2]) -> Vector2:
        """Центр (по bbox) зоны — используется как preferred_direction для
        GND-виа при поиске свободного места (см. plan_vias/keepout)."""
        xs = [p.x for p in boundary_polygon]
        ys = [p.y for p in boundary_polygon]
        return Vector2.from_xy(int((min(xs) + max(xs)) / 2), int((min(ys) + max(ys)) / 2))

    def _find_pad(self, fp, pad_number: str) -> Optional[Pad]:
        return self.adapter.get_pad_by_number(fp, pad_number)

    def _mirror_angle(self, angle_deg: float) -> float:
        if self.cfg.side == "back":
            return 180.0 - angle_deg
        return angle_deg

    def plan_moves(self) -> List[MoveCommand]:
        initial = []   # оптимизатор сам сгенерирует начальное приближение
        final = self.optimizer.optimize(
            initial,
            self._target_fp,
            self._boundary_polygon,
            self.cfg.rules,
            self.cfg.side,
            self._target_layer
        )
        moves = []
        self._planned = []
        for fp in final:
            angle_obj = Angle.from_degrees(fp.angle)
            moves.append(MoveCommand(ref=fp.component.ref, position=fp.position,
                                    angle=angle_obj, layer=self._target_layer))
            self._planned.append((fp.component, fp.position, fp.direction))
        logger.info(f"plan_moves завершено: {len(moves)} перемещений")

        return moves
        
    def plan_vias(self) -> List[ViaCommand]:
        return self.via_planner.plan_vias(
            planned=self._planned,
            target_fp=self._target_fp,
            zone_center_point=self._zone_center_point,
            boundary_polygon=self._boundary_polygon
        )

    def plan(self) -> Tuple[List[MoveCommand], List[ViaCommand]]:
        """
        Обратно совместимая обёртка: plan_moves() + plan_vias() подряд, без
        коммита/перечитывания платы между ними — то есть виа считаются по
        позициям из plan_moves(), ещё не применённым к реальной плате.
        Поведение идентично старому единому plan(). Как только executor
        начнёт по-настоящему коммитить между фазами (следующий шаг), этот
        метод перестанет использоваться для боевого прогона — но останется
        полезным для dry-run и тестов, где коммит не нужен.
        """
        moves = self.plan_moves()
        vias = self.plan_vias()
        return moves, vias
