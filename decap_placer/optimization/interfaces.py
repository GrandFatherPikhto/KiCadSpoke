# decap_placer/optimization/interfaces.py
from abc import ABC, abstractmethod
from typing import List, Tuple
from kipy.geometry import Vector2
from kipy.board_types import BoardLayer
from ..config import SpokeComponent, Rule

# Определим структуру для начальных данных и результата
# (можно использовать именованные кортежи или dataclass'ы)
class RawPlacement:
    def __init__(self, component: SpokeComponent, position: Vector2, direction: Tuple[float, float], angle: float):
        self.component = component
        self.position = position
        self.direction = direction
        self.angle = angle

class FinalPlacement:
    def __init__(self, component: SpokeComponent, position: Vector2, direction: Tuple[float, float], angle: float):
        self.component = component
        self.position = position
        self.direction = direction
        self.angle = angle

class IOptimizer(ABC):
    @abstractmethod
    def optimize(self, initial_placements: List[RawPlacement],
                side: str, target_layer: BoardLayer) -> List[FinalPlacement]:
        pass