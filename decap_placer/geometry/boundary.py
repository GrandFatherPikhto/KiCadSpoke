# decap_placer/geometry/boundary.py

import math
from typing import List, Tuple
from kipy.geometry import Vector2
from ..exceptions import GeometryError

def polyline_points(polyline):
    return [node.point for node in polyline if node.has_point]

def _ray_segment_intersection(ox, oy, dx, dy, x1, y1, x2, y2):
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

def closest_point_on_polygon(point: Vector2, polygon: List[Vector2]) -> Tuple[Vector2, Tuple[float, float]]:
    """
    Находит ближайшую точку на полигоне к заданной точке и нормаль в этой точке.
    Нормаль направлена внутрь полигона (в сторону уменьшения расстояния).
    Возвращает (ближайшая_точка, (нормаль_x, нормаль_y)).
    """
    best_dist = float('inf')
    best_point = None
    best_normal = None  # будет кортежем (nx, ny)

    n = len(polygon)
    for i in range(n):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % n]
        seg_vec = p2 - p1

        seg_len_sq = seg_vec.x * seg_vec.x + seg_vec.y * seg_vec.y
        if seg_len_sq == 0:
            continue

        v = point - p1
        dot_v_seg = v.x * seg_vec.x + v.y * seg_vec.y
        t = dot_v_seg / seg_len_sq
        t = max(0.0, min(1.0, t))
        proj = p1 + seg_vec * t
        dist = (point - proj).length()

        if dist < best_dist:
            best_dist = dist
            best_point = proj

            seg_len = math.sqrt(seg_len_sq)
            if seg_len == 0:
                continue
            # Нормали как кортежи
            normal1 = (seg_vec.y / seg_len, -seg_vec.x / seg_len)
            normal2 = (-seg_vec.y / seg_len, seg_vec.x / seg_len)

            vec_to_proj = proj - point
            dist_to_proj = vec_to_proj.length()
            if dist_to_proj > 0:
                vx = vec_to_proj.x / dist_to_proj
                vy = vec_to_proj.y / dist_to_proj
                dot1 = normal1[0]*vx + normal1[1]*vy
                dot2 = normal2[0]*vx + normal2[1]*vy
                if dot1 > dot2:
                    best_normal = normal1
                else:
                    best_normal = normal2
            else:
                best_normal = normal1

    if best_point is None or best_normal is None:
        raise GeometryError("Не удалось найти ближайшую точку на полигоне")

    return best_point, best_normal