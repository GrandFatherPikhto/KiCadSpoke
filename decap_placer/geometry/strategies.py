# decap_placer/geometry/strategies.py

import math
from abc import ABC, abstractmethod
from typing import List, Tuple
from kipy.geometry import Vector2
from .boundary import ray_boundary_distance
from ..utils.units import MM

class PlacementStrategy(ABC):
    @abstractmethod
    def compute_position(self, center: Vector2, pad_pos: Vector2,
                         boundary_polygon: List[Vector2],
                         placement: str, offset_mm: float,
                         fixed_angle_deg: float = 0.0) -> Tuple[Vector2, Tuple[float, float]]:
        """
        Возвращает (позиция_в_нм, единичный_вектор_направления).
        Для orthogonal и fixed угол влияет только на направление, позиция вычисляется радиально.
        """
        pass

class RadialStrategy(PlacementStrategy):
    def compute_position(self, center, pad_pos, boundary_polygon, placement, offset_mm, fixed_angle_deg=0.0):
        t_boundary, (ux, uy) = ray_boundary_distance(center, pad_pos, boundary_polygon)
        offset = offset_mm * MM

        if placement == "outside":
            t = t_boundary + offset
        elif placement == "boundary":
            t = t_boundary + offset
        elif placement == "inside":
            pad_t = math.hypot(pad_pos.x - center.x, pad_pos.y - center.y)
            t = pad_t - offset
            if t < 0:
                raise ValueError(f"offset_mm={offset_mm} больше расстояния до площадки")
        else:
            raise ValueError(f"неизвестный placement: {placement!r}")

        point = Vector2.from_xy(int(center.x + ux * t), int(center.y + uy * t))
        return point, (ux, uy)

class OrthogonalStrategy(PlacementStrategy):
    def compute_position(self, center, pad_pos, boundary_polygon, placement, offset_mm, fixed_angle_deg=0.0):
        # Сначала радиально
        point, (ux, uy) = RadialStrategy().compute_position(center, pad_pos, boundary_polygon, placement, offset_mm)
        phi_deg = math.degrees(math.atan2(uy, ux))
        phi_deg = round(phi_deg / 90.0) * 90.0
        rad = math.radians(phi_deg)
        return point, (math.cos(rad), math.sin(rad))

class FixedStrategy(PlacementStrategy):
    def compute_position(self, center, pad_pos, boundary_polygon, placement, offset_mm, fixed_angle_deg=0.0):
        # Позиция радиальная, направление фиксированное
        point, _ = RadialStrategy().compute_position(center, pad_pos, boundary_polygon, placement, offset_mm)
        rad = math.radians(fixed_angle_deg)
        return point, (math.cos(rad), math.sin(rad))