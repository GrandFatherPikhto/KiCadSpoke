# decap_placer/geometry/boundary.py

import math
from typing import List, Optional, Tuple
from kipy.geometry import Vector2
from ..exceptions import GeometryError

def polyline_points(polyline):
    """Извлекает точки из полилинии (без учёта дуг)."""
    return [node.point for node in polyline if node.has_point]

def _ray_segment_intersection(ox, oy, dx, dy, x1, y1, x2, y2):
    """Пересечение луча (o + t*d) с отрезком (p1-p2). Возвращает t или None."""
    ex, ey = x2 - x1, y2 - y1
    denom = dx * ey - dy * ex
    if abs(denom) < 1e-9:
        return None
    t = ((x1 - ox) * ey - (y1 - oy) * ex) / denom
    s = ((x1 - ox) * dy - (y1 - oy) * dx) / denom
    if 0 <= s <= 1:
        return t
    return None

def ray_boundary_distance(center: Vector2, target: Vector2, boundary_pts: List[Vector2]) -> Tuple[float, Tuple[float, float]]:
    """
    Расстояние (во внутренних единицах) от center до ближайшего пересечения
    луча center->target с границей полигона. Возвращает (t, (ux, uy)).
    """
    dx, dy = target.x - center.x, target.y - center.y
    length = math.hypot(dx, dy)
    if length == 0:
        raise GeometryError("center и target совпадают – невозможно определить направление")
    ux, uy = dx / length, dy / length

    best_t = None
    n = len(boundary_pts)
    for i in range(n):
        p1, p2 = boundary_pts[i], boundary_pts[(i + 1) % n]
        t = _ray_segment_intersection(center.x, center.y, ux, uy, p1.x, p1.y, p2.x, p2.y)
        if t is not None and t > 0 and (best_t is None or t < best_t):
            best_t = t
    if best_t is None:
        raise GeometryError("луч не пересекает границу зоны – проверьте геометрию/сторону")
    return best_t, (ux, uy)