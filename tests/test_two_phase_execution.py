#!/usr/bin/env python3
"""
Регрессия на двухфазное выполнение (execute_moves -> adapter.refresh_board() ->
plan_vias -> execute_vias), как в kicadspoke_cli.py:cmd_apply.

ПЕРЕОСМЫСЛЕНО (KiCadSpoke, обобщённые via, 2026-07-15): раньше этот тест
проверял, что plan_vias() видит РЕАЛЬНЫЙ (перечитанный) пад компонента
после коммита перемещений — то была защита от бага, при котором GND via
считалась от старой, ещё не сдвинутой позиции. Теперь via (обоих уровней)
— чистая геометрия, вычисляется В МОМЕНТ plan_moves(), никакого чтения
живого пада компонента для неё вообще не требуется — сама проблема,
от которой защищал этот тест, структурно больше не может возникнуть.

Тест теперь проверяет, что сам двухфазный поток по-прежнему отрабатывает
целиком без ошибок и даёт геометрически верные позиции (сверено с
независимым расчётом).
"""
import sys
import math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2, Angle
from kipy.board_types import BoardLayer, Pad, Net

from kicadspoke.config import (
    Config, ThermalViaArrayConfig, ManualSpoke, SpokeTemplate,
    TemplateVia, TemplateComponentSlot, Rule
)
from kicadspoke.placement.planner import PlacementPlanner
from kicadspoke.placement.executor import BatchExecutor
from kicadspoke.geometry.spoke_layout import rotate_local_offset
from kicadspoke.constants import SPOKE_LEVEL_ROLE_PLACEHOLDER

MM = 1_000_000


def _make_pad(number, x_mm, y_mm, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net.name = net_name
    return pad


def test_two_phase_flow_completes_and_via_geometry_is_correct():
    template = SpokeTemplate(
        name="t",
        components=[TemplateComponentSlot(
            role="LIGHT",
            offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=0.0,
            vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=0.5, net="GND")],
        )],
    )
    spoke = ManualSpoke(pad="17", template="t", rotation_deg=0.0)
    cfg = Config(
        layer='B.Cu',
        templates={"t": template},
        thermal_via_array=ThermalViaArrayConfig(enabled=False),
        rules=[Rule(net="+3V3", anchor_ref='IC1', spokes=[spoke])],
    )

    ic1 = MagicMock()
    ic1.reference_field.text.value = "IC1"
    pad_pos = Vector2.from_xy(int(50.0 * MM), int(50.0 * MM))
    ic1.definition.items = [_make_pad("17", 50.0, 50.0, "+3V3")]

    c5 = MagicMock()
    c5.reference_field.text.value = "C5"
    c5.position = Vector2.from_xy(0, 0)
    c5.orientation = Angle.from_degrees(0.0)
    c5.layer = BoardLayer.BL_F_Cu
    c5.definition.items = [_make_pad("1", 0.0, 0.0, "+3V3"), _make_pad("2", 0.0, 0.0, "GND")]
    c5._role = "LIGHT"

    net_gnd = Net(name="GND")
    net_power = Net(name="+3V3")

    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: ic1 if ref == "IC1" else (c5 if ref == "C5" else None)
    adapter.get_footprints.return_value = [ic1, c5]
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in fp.definition.items if p.number == num), None
    )
    adapter.get_footprint_pads.side_effect = lambda fp: list(fp.definition.items)
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    adapter.get_net_by_name.side_effect = lambda name: net_gnd if name == "GND" else (
        net_power if name == "+3V3" else None
    )
    adapter.get_bounding_boxes.return_value = []
    adapter.commit_with_retry.return_value = True
    adapter.get_vias.return_value = []

    planner = PlacementPlanner(adapter, cfg)
    executor = BatchExecutor(adapter, cfg, batch_size=10)

    # Тот самый порядок из kicadspoke_cli.py:cmd_apply
    moves = planner.plan_moves()
    assert len(moves) == 1
    executor.execute_moves(moves, check_collisions=False)
    adapter.refresh_board()
    vias = planner.plan_vias()
    executor.execute_vias(vias)

    gnd_vias = [v for v in vias if v.owner_ref == "C5"]
    assert len(gnd_vias) == 1
    via = gnd_vias[0]

    # via — чистая геометрия от нуля спицы (pad_pos), сверяем независимым расчётом
    expected_offset = rotate_local_offset(0.0, 0.5, 0.0)
    expected_x = pad_pos.x + expected_offset.x
    expected_y = pad_pos.y + expected_offset.y
    assert via.position.x == expected_x
    assert via.position.y == expected_y
    assert via.net_name == "GND"

    # Проверяем, что registry_key заполнен (важно для идемпотентности)
    assert via.registry_key is not None
    # Для via уровня компонента роль LIGHT, а не SPOKE_LEVEL
    assert "LIGHT" in via.registry_key
    assert SPOKE_LEVEL_ROLE_PLACEHOLDER not in via.registry_key