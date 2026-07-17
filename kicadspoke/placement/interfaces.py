from abc import ABC, abstractmethod
from typing import List, Tuple
from kipy.board_types import FootprintInstance, BoardLayer
from ..config import Rule
from .commands import PlacedComponentInfo, ViaCommand

class IPositionCalculator(ABC):
    @abstractmethod
    def compute_raw_positions(
        self,
        target_fp: FootprintInstance,
        rules: List[Rule],
        side: str
    ) -> Tuple[List[PlacedComponentInfo], List[ViaCommand]]:
        """Расчёт позиций компонентов и via."""
        pass

class IViaPlanner(ABC):
    @abstractmethod
    def plan_vias(
        self,
        planned_components: List[PlacedComponentInfo],
        planned_vias: List[ViaCommand],
        target_fp: FootprintInstance,
        target_layer: BoardLayer
    ) -> List[ViaCommand]:
        """Планирование via (термовиа + фильтрация)."""
        pass