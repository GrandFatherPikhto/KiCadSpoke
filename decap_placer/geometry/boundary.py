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

def polygon_signed_area(polygon: List[Vector2]) -> float:
    """
    Удвоенная знаковая площадь полигона (формула shoelace). Знак даёт
    ориентацию обхода (CW/CCW) — используется для того, чтобы определить
    направление "наружу" ОДИН РАЗ для всего полигона, а не для каждой
    опрашиваемой точки отдельно (см. ИСПРАВЛЕНО ниже).
    """
    n = len(polygon)
    area = 0.0
    for i in range(n):
        p1, p2 = polygon[i], polygon[(i + 1) % n]
        area += p1.x * p2.y - p2.x * p1.y
    return area


def closest_point_on_polygon(point: Vector2, polygon: List[Vector2]) -> Tuple[Vector2, Tuple[float, float]]:
    """
    Находит ближайшую точку на полигоне к заданной точке и нормаль,
    направленную НАРУЖУ от полигона, в этой точке.
    Возвращает (ближайшая_точка, (нормаль_x, нормаль_y)).

    ИСПРАВЛЕНО (2026-07-12): раньше направление нормали (какая из двух
    перпендикулярных кандидатур — "наружу" или "внутрь") выбиралось по
    тому, с какой стороны от границы находится ОПРАШИВАЕМАЯ точка (пад) —
    "какая нормаль сильнее совпадает с направлением от точки к границе".
    Это ломалось на пограничном случае: если сам вывод IC1 оказывался
    на волосок СНАРУЖИ зоны (реальный случай — зона нарисована впритык,
    вывод в 0.008мм за границей), выбор нормали переворачивался на 180°
    именно для этого вывода — что и давало "inside/outside перепутаны"
    ровно на одной стороне корпуса, где зона прочерчена не с запасом.

    Теперь направление "наружу" вычисляется ОДИН РАЗ для всего полигона
    через ориентацию обхода (знаковую площадь, см. polygon_signed_area) —
    результат не зависит от того, где именно относительно границы стоит
    конкретная опрашиваемая точка, и на пограничных случаях не ломается.
    """
    if len(polygon) < 3:
        raise GeometryError("полигон должен содержать хотя бы 3 точки")

    signed_area = polygon_signed_area(polygon)
    if signed_area == 0:
        raise GeometryError("вырожденный полигон (нулевая площадь) — проверьте геометрию зоны")
    # signed_area > 0 (в координатах KiCad, Y вниз) — обход по часовой
    # стрелке в привычном "на экране" смысле; знак сам по себе нам не
    # важен как таковой — важно лишь то, что ОДНА и та же формула
    # применяется консистентно для всех сторон одного и того же полигона.
    orientation_sign = 1.0 if signed_area > 0 else -1.0

    best_dist = float('inf')
    best_point = None
    best_normal = None

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
            # Нормаль "наружу" для ЭТОГО ребра, консистентная для всего
            # полигона через orientation_sign — не зависит от точки point.
            nx = orientation_sign * seg_vec.y / seg_len
            ny = -orientation_sign * seg_vec.x / seg_len
            best_normal = (nx, ny)

    if best_point is None or best_normal is None:
        raise GeometryError("Не удалось найти ближайшую точку на полигоне")

    return best_point, best_normal