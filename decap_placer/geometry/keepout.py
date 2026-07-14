# decap_placer/geometry/keepout.py

import math
from typing import List, Tuple, Optional
from kipy.geometry import Vector2


class Rect:
    """Простой AABB-прямоугольник в координатах платы (нм)."""

    def __init__(self, min_x: float, min_y: float, max_x: float, max_y: float):
        self.min_x, self.min_y, self.max_x, self.max_y = min_x, min_y, max_x, max_y

    @classmethod
    def from_bbox(cls, bbox, clearance: int = 0) -> "Rect":
        """Строит Rect из Box2 (см. adapter.get_bounding_boxes), с запасом clearance с каждой стороны."""
        return cls(
            bbox.pos.x - clearance, bbox.pos.y - clearance,
            bbox.pos.x + bbox.size.x + clearance, bbox.pos.y + bbox.size.y + clearance,
        )

    @classmethod
    def from_circle(cls, center: Vector2, radius: float) -> "Rect":
        """Грубое (но простое и быстрое) приближение окружности квадратом — для
        via с её малым диаметром этого достаточно, не нужен точный circle-vs-rect тест."""
        return cls(center.x - radius, center.y - radius, center.x + radius, center.y + radius)

    def intersects(self, other: "Rect") -> bool:
        return not (self.max_x < other.min_x or other.max_x < self.min_x or
                    self.max_y < other.min_y or other.max_y < self.min_y)

    def __repr__(self):
        return f"Rect({self.min_x}, {self.min_y}, {self.max_x}, {self.max_y})"


def point_is_clear(point: Vector2, via_radius: float, keepout: List[Rect]) -> bool:
    """True, если окружность via_radius вокруг point не пересекает ни один keepout-прямоугольник."""
    via_box = Rect.from_circle(point, via_radius)
    return not any(via_box.intersects(r) for r in keepout)


def build_keepout(bboxes, clearance_mm: float, mm_per_unit: int = 1_000_000) -> List[Rect]:
    """
    Строит список Rect из bounding box'ов (см. adapter.get_bounding_boxes),
    с запасом clearance_mm с каждой стороны. None-элементы (bbox
    недоступен для конкретного пада/футпринта) молча пропускаются —
    вызывающий код может залогировать это отдельно, если нужно.
    """
    clearance = int(clearance_mm * mm_per_unit)
    rects = []
    for bbox in bboxes:
        if bbox is None:
            continue
        rects.append(Rect.from_bbox(bbox, clearance))
    return rects


def find_free_point(
    ideal: Vector2,
    keepout: List[Rect],
    via_radius: float,
    preferred_direction: Optional[Tuple[float, float]] = None,
    step_mm: float = 0.1,
    max_radius_mm: float = 3.0,
    mm_per_unit: int = 1_000_000,
    n_directions: int = 8,
) -> Optional[Vector2]:
    """
    Ищет ближайшую к ideal свободную точку (не задевающую keepout)
    расширяющимися кольцами: сначала сама ideal, затем кольцо радиусом
    step_mm, 2*step_mm, ... до max_radius_mm.

    На каждом кольце сперва пробуется preferred_direction (если задано —
    например, "в сторону центра зоны" для GND-виа по умолчанию), затем
    n_directions точек равномерно по кругу. Первая подошедшая точка
    возвращается сразу — не обязательно глобально ближайшая, но
    гарантированно в пределах текущего (самого маленького из пройденных)
    кольца.

    Возвращает None, если свободного места не нашлось в пределах
    max_radius_mm — вызывающий код должен считать это предупреждением/
    ошибкой, а не пытаться поставить виа как попало.
    """
    step = step_mm * mm_per_unit
    max_radius = max_radius_mm * mm_per_unit

    if point_is_clear(ideal, via_radius, keepout):
        return ideal

    ring = step
    while ring <= max_radius + 1e-6:
        candidates_deg: List[float] = []
        if preferred_direction is not None:
            pdx, pdy = preferred_direction
            candidates_deg.append(math.degrees(math.atan2(pdy, pdx)))
        for i in range(n_directions):
            candidates_deg.append(360.0 * i / n_directions)

        for deg in candidates_deg:
            rad = math.radians(deg)
            candidate = Vector2.from_xy(
                int(ideal.x + ring * math.cos(rad)),
                int(ideal.y + ring * math.sin(rad)),
            )
            if point_is_clear(candidate, via_radius, keepout):
                return candidate

        ring += step

    return None
