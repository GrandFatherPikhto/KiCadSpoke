# decap_placer/placement/services/position_calculator.py
import math
import logging
from typing import List, Tuple, Dict, Any
from kipy.board_types import Pad, FootprintInstance
from kipy.geometry import Vector2

from ...config import Config, Rule, Spoke, SpokeComponent
from ...geometry.strategies import (
    PlacementStrategy, RadialStrategy, OrthogonalStrategy,
    FixedStrategy, BoundaryStrategy
)
from ...geometry.boundary import polyline_points
from ...geometry.strategy_factory import StrategyFactory
from ...kicad.adapter import KiCadBoardAdapter

from ...utils.units import MM

from ...exceptions import GeometryError

logger = logging.getLogger(__name__)

class PositionCalculator:
    """
    Вычисляет сырые позиции конденсаторов (без раздвижки и коррекции угла)
    на основе стратегии размещения.
    """

    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config
        self._strategy = StrategyFactory.create(config.rotation_mode, config.fixed_angle_deg)

    def _create_strategy(self) -> PlacementStrategy:
        mode = self.cfg.rotation_mode
        if mode == "radial":
            return RadialStrategy()
        elif mode == "orthogonal":
            return OrthogonalStrategy()
        elif mode == "fixed":
            return FixedStrategy()
        elif mode == "boundary":
            return BoundaryStrategy()
        else:
            raise ValueError(f"Неизвестный rotation_mode: {mode}")

    def compute_raw_positions(
        self,
        target_fp: FootprintInstance,
        boundary_polygon: List[Vector2],
        rules: List[Rule],
        side: str
    ) -> List[Tuple[SpokeComponent, Vector2, Tuple[float, float], float]]:
        """
        Для каждого компонента вычисляет:
        - dest: позиция (Vector2)
        - direction: единичный вектор направления (nx, ny)
        - angle_base: сырой угол в градусах (до коррекции power_pin)
        Возвращает список кортежей (component, dest, direction, angle_base)
        """
        center = target_fp.position
        raw = []

        for rule in rules:
            net = self.adapter.get_net_by_name(rule.net)
            if net is None:
                logger.warning(f"Цепь {rule.net} не найдена, пропускаем")
                continue

            for spoke in rule.spokes:
                pad = self.adapter.get_pad_by_number(target_fp, spoke.pad)
                if pad is None:
                    logger.warning(
                        f"У {self.cfg.target_ref} нет площадки {spoke.pad}, "
                        f"пропуск всей спицы ({len(spoke.components)} компонент.)"
                    )
                    continue

                for component in spoke.components:
                    try:
                        dest, direction = self._strategy.compute_position(
                            center,
                            pad.position,
                            boundary_polygon,
                            component.placement,
                            component.offset_mm,
                            fixed_angle_deg=self.cfg.fixed_angle_deg
                        )
                    except GeometryError as e:
                        raise GeometryError(f"Ошибка для {component.ref} (спица {spoke.pad}): {e}")

                    phi_deg = math.degrees(math.atan2(direction[1], direction[0]))
                    # Зеркалирование угла для back-стороны
                    if side == "back":
                        phi_deg = 180.0 - phi_deg

                    raw.append((component, dest, direction, phi_deg))
                    logger.debug(
                        f"  {component.ref} (спица {spoke.pad}, сырая позиция) -> "
                        f"({dest.x/MM:.3f}, {dest.y/MM:.3f}) мм, угол={phi_deg:.1f}°"
                    )

        return raw