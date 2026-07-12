# decap_placer/geometry/thermal_grid.py

import math
from typing import List
from kipy.board_types import Pad
from kipy.geometry import Vector2
from ..utils.units import MM
from ..exceptions import GeometryError

def get_pad_size(pad: Pad) -> tuple:
    """Возвращает (width, height) медного слоя падстека."""
    layers = pad.padstack.copper_layers
    if not layers:
        raise GeometryError("у площадки нет медных слоёв в падстеке")
    size = layers[0].size
    return size.x, size.y

def compute_thermal_via_grid(pad: Pad, rows: int, cols: int, margin_mm: float, stagger: bool = False) -> List[Vector2]:
    """Возвращает список абсолютных позиций для виа."""
    if rows < 1 or cols < 1:
        raise GeometryError("rows и cols должны быть >= 1")

    width, height = get_pad_size(pad)
    margin = margin_mm * MM
    usable_w = width - 2 * margin
    usable_h = height - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        raise GeometryError(f"margin_mm={margin_mm} слишком большой для площадки {width/MM:.2f}x{height/MM:.2f} мм")

    local_points = []
    for r in range(rows):
        y = 0 if rows == 1 else -usable_h/2 + usable_h * r / (rows - 1)
        row_offset = (usable_w / (cols * 2)) if (stagger and cols > 1 and r % 2 == 1) else 0
        for c in range(cols):
            x = 0 if cols == 1 else -usable_w/2 + usable_w * c / (cols - 1)
            local_points.append((x + row_offset, y))

    angle_rad = pad.padstack.angle.to_radians()
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    abs_points = []
    for lx, ly in local_points:
        rx = lx * cos_a - ly * sin_a
        ry = lx * sin_a + ly * cos_a
        abs_points.append(Vector2.from_xy(int(pad.position.x + rx), int(pad.position.y + ry)))
    return abs_points