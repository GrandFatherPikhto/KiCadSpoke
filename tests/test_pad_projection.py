#!/usr/bin/env python3
"""
Регрессионные тесты на pad_projection.py — единая точка расчёта позиции
пада после переноса, поворота и/или зеркалирования компонента.

В текущей архитектуре via вычисляются геометрически на этапе планирования
и не используют pad_projection напрямую, но модуль остаётся важным для
других операций (например, при работе с падами вручную или для будущих
расширений).
"""
import sys
import math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2, Angle
from kipy.board_types import BoardLayer


from kicadspoke.geometry.pad_projection import predict_pad_position, local_pad_offset
from kicadspoke.utils.units import MM


def _make_fp(x_mm, y_mm, angle_deg, layer):
    fp = MagicMock()
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp.orientation = Angle.from_degrees(angle_deg)
    fp.layer = layer
    return fp


def _make_pad(x_mm, y_mm, net_name="GND"):
    pad = MagicMock()
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net.name = net_name
    return pad


def _rotate_point(x_mm, y_mm, angle_deg):
    """Поворот точки (x,y) на угол angle_deg по часовой стрелке (конвенция KiCad)."""
    theta = math.radians(angle_deg)
    rx = x_mm * math.cos(theta) + y_mm * math.sin(theta)
    ry = -x_mm * math.sin(theta) + y_mm * math.cos(theta)
    return rx, ry


class TestPredictPadPosition:
    def test_no_move_no_rotate_no_flip_returns_same_offset(self):
        """Без переноса/поворота/флипа предсказанная позиция пада должна
        совпадать с исходной абсолютной позицией пада."""
        fp = _make_fp(50.0, 50.0, 0.0, BoardLayer.BL_F_Cu)
        pad = _make_pad(50.5675, 50.0)
        dest = fp.position  # та же позиция
        result = predict_pad_position(fp, pad, dest, angle_deg=0.0, needs_flip=False)
        assert abs(result.x - pad.position.x) < 10
        assert abs(result.y - pad.position.y) < 10

    def test_rotation_changes_predicted_position(self):
        """Поворот на 90° должен реально сдвинуть предсказанную позицию
        пада, а не оставить её равной исходному абсолютному оффсету."""
        fp = _make_fp(50.0, 50.0, 0.0, BoardLayer.BL_F_Cu)
        pad = _make_pad(50.5675, 50.0)
        dest = Vector2.from_xy(int(70.0 * MM), int(30.0 * MM))

        predicted_0deg = predict_pad_position(fp, pad, dest, angle_deg=0.0, needs_flip=False)
        predicted_90deg = predict_pad_position(fp, pad, dest, angle_deg=90.0, needs_flip=False)

        dist_mm = math.hypot(predicted_0deg.x - predicted_90deg.x,
                             predicted_0deg.y - predicted_90deg.y) / MM
        assert dist_mm > 0.3, "поворот на 90° должен заметно изменить предсказанную позицию пада"

    def test_flip_mirrors_local_x_before_rotation(self):
        """needs_flip=True должен зеркалировать локальный X ДО применения
        нового угла — проверяем через local_pad_offset (постоянный факт о
        геометрии) и сравнение результатов с флипом/без."""
        fp = _make_fp(50.0, 50.0, 0.0, BoardLayer.BL_F_Cu)
        pad = _make_pad(50.5675, 50.0)  # локальный оффсет (+0.5675, 0)
        dest = fp.position

        offset = local_pad_offset(fp, pad)
        assert abs(offset.x / MM - 0.5675) < 1e-3
        assert abs(offset.y / MM) < 1e-3

        no_flip = predict_pad_position(fp, pad, dest, angle_deg=0.0, needs_flip=False)
        with_flip = predict_pad_position(fp, pad, dest, angle_deg=0.0, needs_flip=True)
        # При угле 0° и флипе результат должен быть ЗЕРКАЛЬНЫМ по X
        # относительно dest, а не совпадать с no_flip.
        assert (no_flip.x - dest.x) == -(with_flip.x - dest.x)

    def test_local_pad_offset_independent_of_current_angle(self):
        """Проверяем, что local_pad_offset возвращает одно и то же
        локальное смещение независимо от текущего угла поворота компонента.
        Для этого задаём локальное смещение (lx, ly) = (1,2), вычисляем
        абсолютную позицию пада при каждом угле, затем проверяем, что
        local_pad_offset даёт (1,2)."""
        lx, ly = 1.0, 2.0
        for angle_deg in (0.0, 45.0, 90.0, 180.0):
            fp = _make_fp(0.0, 0.0, angle_deg, BoardLayer.BL_F_Cu)
            rx, ry = _rotate_point(lx, ly, angle_deg)
            pad = _make_pad(rx, ry)
            offset = local_pad_offset(fp, pad)
            assert abs(offset.x / MM - lx) < 1e-3, f"angle {angle_deg}: X не совпадает"
            assert abs(offset.y / MM - ly) < 1e-3, f"angle {angle_deg}: Y не совпадает"

    def test_predict_with_pre_rotated_component(self):
        """Проверяем, что предсказание работает корректно, если компонент
        уже был повёрнут, и мы применяем новый поворот."""
        lx, ly = 0.5675, 0.0
        initial_angle = 45.0
        # Компонент в (50,50) с углом 45°
        fp = _make_fp(50.0, 50.0, initial_angle, BoardLayer.BL_F_Cu)
        # Абсолютная позиция пада при начальном угле
        rx, ry = _rotate_point(lx, ly, initial_angle)
        pad = _make_pad(50.0 + rx, 50.0 + ry)
        dest = Vector2.from_xy(int(70.0 * MM), int(30.0 * MM))

        # Предсказываем позицию пада, если компонент переместить в dest с углом 0°
        predicted = predict_pad_position(fp, pad, dest, angle_deg=0.0, needs_flip=False)
        expected_x = 70.0 + lx  # локальное смещение не меняется
        expected_y = 30.0 + ly
        assert abs(predicted.x / MM - expected_x) < 1e-3
        assert abs(predicted.y / MM - expected_y) < 1e-3