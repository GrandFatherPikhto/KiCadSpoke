# decap_placer/geometry/strategies.py

import math
from abc import ABC, abstractmethod
from typing import List, Tuple
from kipy.geometry import Vector2
from .boundary import ray_boundary_distance, closest_point_on_polygon
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

class BoundaryStrategy(PlacementStrategy):
    """
    ИСПРАВЛЕНО (2026-07-12): outside/inside были перепутаны местами.

    closest_point_on_polygon() возвращает normal, направленную НАРУЖУ от
    полигона (когда исходная точка — площадка — лежит внутри зоны, что и
    есть наш реальный случай: IC1 нарисован внутри RA_DECAP_ZONE).
    Проверено расчётом на реальных координатах зоны: для точки на границе
    (163.31, 59.28) с normal=(0,-1) (наружу, в сторону уменьшения Y) —
    старая версия "outside" двигала точку в сторону +Y (обратно ВНУТРЬ
    зоны, к площадке), а "inside" — в сторону -Y (НАРУЖУ, за границу).
    Т.е. ветки были буквально перепутаны. Правильно:
        outside = точка_на_границе + normal*offset  (дальше НАРУЖУ)
        inside  = точка_на_границе - normal*offset  (внутрь, к центру)
    """
    def compute_position(self, center: Vector2, pad_pos: Vector2,
                         boundary_polygon: List[Vector2],
                         placement: str, offset_mm: float,
                         fixed_angle_deg: float = 0.0) -> Tuple[Vector2, Tuple[float, float]]:
        point_on_boundary, normal = closest_point_on_polygon(pad_pos, boundary_polygon)
        nx, ny = normal
        offset = offset_mm * MM
        if placement == "outside":
            point = Vector2.from_xy(int(point_on_boundary.x + nx * offset),
                                    int(point_on_boundary.y + ny * offset))
        elif placement == "inside":
            point = Vector2.from_xy(int(point_on_boundary.x - nx * offset),
                                    int(point_on_boundary.y - ny * offset))
        else:
            point = point_on_boundary
        return point, (nx, ny)
