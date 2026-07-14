# decap_placer/optimization/heuristic_optimizer.py
from typing import List
from .interfaces import IOptimizer, FinalPlacement
from ..placement.services.position_calculator import PositionCalculator
from ..placement.services.power_pin_orienter import PowerPinOrienter
from ..placement.services.spacing_relaxer import SpacingRelaxer
from ..kicad.adapter import KiCadBoardAdapter
from ..config import Config

from kipy.board_types import BoardLayer

class HeuristicOptimizer(IOptimizer):
    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.position_calc = PositionCalculator(adapter, config)
        self.power_pin_orienter = PowerPinOrienter(adapter, config)
        self.spacing_relaxer = SpacingRelaxer(adapter, config)

    def optimize(self, initial_placements, target_fp, boundary_polygon, rules, side: str, target_layer: BoardLayer):
        # initial_placements здесь не используются, потому что мы всё пересчитываем с нуля
        raw = self.position_calc.compute_raw_positions(target_fp, boundary_polygon, rules, side)
        raw = self.power_pin_orienter.adjust_angles(raw, target_fp, target_layer, rules)
        relaxed = self.spacing_relaxer.relax(raw)
        # Преобразуем в FinalPlacement
        result = []
        for new_pos, (component, direction, angle) in relaxed:
            result.append(FinalPlacement(component, new_pos, direction, angle))
        return result
    
    def _compute_raw_placements(self, target_fp, boundary_polygon, rules, side):
        raw = self.position_calc.compute_raw_positions(target_fp, boundary_polygon, rules, side)
        # Здесь мы не применяем power_pin_orienter и не раздвигаем, просто возвращаем сырые
        return raw
    
    def generate_initial_placements(self, target_fp, boundary_polygon, rules, side):
        raw = self.position_calc.compute_raw_positions(target_fp, boundary_polygon, rules, side)
        # Применяем коррекцию углов (без раздвижки)
        raw = self.power_pin_orienter.adjust_angles(raw, target_fp, self._target_layer, rules)  # но target_layer у нас нет
        # Здесь сложно, потому что нужен target_layer. Передадим его.