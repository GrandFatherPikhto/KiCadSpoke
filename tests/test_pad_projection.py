#!/usr/bin/env python3
"""
Регрессионные тесты на два исправления (2026-07-15):

1. pad_projection.predict_pad_position — единая точка расчёта, где
   окажется пад компонента после переноса+поворота(+флипа). Раньше
   power_pin_orienter.py и via_planner.py либо содержали копию этой
   логики, либо (в случае via_planner) вообще не учитывали новый угол —
   отсюда виа на площадках конденсаторов при почти любом развороте.

2. via_planner.ViaPlanner._plan_stitching_vias — теперь принимает угол
   поворота и слой назначения, и использует predict_pad_position вместо
   слепого переноса устаревшего абсолютного смещения пада.
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

