import sys
from pathlib import Path
from typing import List, Tuple


def generate_placements_on_circle(
    radius: float,
    start_angle_deg: float,
    count: int,
    step_deg: float = 90.0
) -> List[Tuple[float, float, float]]:
    """
    Генерирует точки на окружности радиуса radius,
    начиная с угла start_angle_deg, с шагом step_deg.
    Угол компонента равен углу точки.
    """
    import math
    result = []
    for i in range(count):
        angle = math.radians(start_angle_deg + i * step_deg)
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        result.append((round(x, 4), round(y, 4), round(math.degrees(angle), 4)))
    return result

def generate_rotated_placements(
    start_x: float,
    start_y: float,
    angles = (0.0, 90.0, 270.0)
) -> List[Tuple[float, float, float]]:
    """
    Генерирует координаты и углы путём последовательного поворота
    начальной точки на step_deg градусов вокруг (0,0).
    
    Args:
        start_x, start_y: начальные координаты (смещение от центра)
        start_angle: начальный угол компонента (в градусах)
        count: количество точек (минимум 1)
        step_deg: шаг поворота в градусах (по умолчанию 90°)
    
    Returns:
        Список кортежей (x, y, angle)
    """
    import math
    result = []
    x, y = start_x, start_y
    for angle in angles:
        # result.append((round(x, 4), round(y, 4), round(angle, 4)))
        # Поворачиваем вектор (x, y) на step_deg по часовой стрелке
        rad = math.radians(angle)
        new_x = x * math.cos(rad) + y * math.sin(rad)
        new_y = -x * math.sin(rad) + y * math.cos(rad)
        # x, y = new_x, new_y
        result.append((round(new_x, 4), round(new_y, 4), round(angle, 4)))
    return result

angles=(0.0, 90.0, 180.0)

points = generate_rotated_placements(-2.0, 0.0, angles)

print(f"Получилось : {points}")
orig = [
    (-2.0, 0.0, 0.0),
    (0.0, 2.0, 90.0),
    (2.0, 0.0, 180.0)
]
print(f"Должно быть: {orig}")