# kicadspoke/geometry/placement.py
"""
Единственная стратегия размещения – привязка к границе зоны (boundary).
Вычисляет позицию компонента относительно границы полигона зоны.
Используется вместо всех старых стратегий (radial, orthogonal, fixed).
"""

from typing import Tuple
from kipy.geometry import Vector2
from .boundary import closest_point_on_polygon
from ..utils.units import MM   # исправленный импорт


def compute_position(
    center: Vector2,          # не используется, оставлен для совместимости сигнатуры
    pad_pos: Vector2,
    boundary_polygon: list,
    placement: str,           # "inside" или "outside"
    offset_mm: float
) -> Tuple[Vector2, Tuple[float, float]]:
    """
    Возвращает (позиция_в_нм, (нормаль_x, нормаль_y)).
    Всегда использует стратегию "boundary" – привязку к границе зоны.
    """
    point_on_boundary, normal = closest_point_on_polygon(pad_pos, boundary_polygon)
    nx, ny = normal
    offset = offset_mm * MM

    if placement == "outside":
        point = Vector2.from_xy(
            int(point_on_boundary.x + nx * offset),
            int(point_on_boundary.y + ny * offset)
        )
    elif placement == "inside":
        point = Vector2.from_xy(
            int(point_on_boundary.x - nx * offset),
            int(point_on_boundary.y - ny * offset)
        )
    else:
        point = point_on_boundary

    return point, (nx, ny)