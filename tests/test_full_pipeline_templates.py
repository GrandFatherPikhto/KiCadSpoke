#!/usr/bin/env python3
"""
Интеграционный тест конвейера KiCadSpoke целиком: PlacementPlanner
(manual_position_calculator + component_pool + via_planner) на моках.

KiCadSpoke, обобщённые via: via уровня спицы и уровня компонента теперь
вычисляются ОДНОВременно с позициями компонентов, в plan_moves() —
никакого чтения живого пада компонента для via больше не требуется.
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
from kicadspoke.geometry.spoke_layout import rotate_local_offset
from kicadspoke.constants import SPOKE_LEVEL_ROLE_PLACEHOLDER

MM = 1_000_000


def _make_pad(number, x_mm, y_mm, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net.name = net_name
    return pad


def _make_ic1_fp(pads_config):
    """pads_config: список (number, x_mm, y_mm, net_name)"""
    fp = MagicMock()
    fp.reference_field.text.value = "IC1"
    fp.definition.items = [_make_pad(*p) for p in pads_config]
    return fp


def _make_cap_fp(ref, net_name, role):
    """Мок футпринта конденсатора -- ДЛЯ ПУЛА нужны только ref/роль/цепь,
    реальная позиция более не важна для via (вычисляется геометрически)."""
    fp = MagicMock()
    fp.reference_field.text.value = ref
    fp.definition.items = [_make_pad("1", 0, 0, net_name), _make_pad("2", 0, 0, "GND")]
    fp._role = role
    return fp


def _build_config():
    template = SpokeTemplate(
        name="cap_pair_standard",
        vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=-1.5, drill_mm=0.3, diameter_mm=0.6)],
        components=[
            TemplateComponentSlot(
                role="LIGHT",
                offset_along_mm=1.0, offset_across_mm=-1.0, angle_deg=90.0,
                vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=-1.0, net="GND",
                                 drill_mm=0.3, diameter_mm=0.6)],
            ),
            TemplateComponentSlot(
                role="HEAVY",
                offset_along_mm=1.0, offset_across_mm=2.0, angle_deg=270.0,
                vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=1.3, net="GND",
                                 drill_mm=0.3, diameter_mm=0.6)],
            ),
        ],
    )
    spoke_109 = ManualSpoke(pad="109", template="cap_pair_standard",
                           shift_x_mm=0.0, shift_y_mm=0.0, rotation_deg=90.0)
    spoke_62 = ManualSpoke(pad="62", template="cap_pair_standard",
                          shift_x_mm=0.4, shift_y_mm=0.0, rotation_deg=270.0)
    cfg = Config(
        layer='B.Cu',
        templates={"cap_pair_standard": template},
        thermal_via_array=ThermalViaArrayConfig(enabled=False),
        rules=[Rule(net="+1V2_VCCINT", anchor_ref='IC1', spokes=[spoke_109, spoke_62])],
        via_keepout_clearance_mm=0.2, via_search_step_mm=0.1,
        via_search_max_radius_mm=3.0, via_search_n_directions=8,
    )
    return cfg


def _make_pool_adapter(ic1, cap_fps):
    all_fps = [ic1] + cap_fps
    fps_by_ref = {fp.reference_field.text.value: fp for fp in all_fps}

    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: fps_by_ref.get(ref)
    adapter.get_footprints.return_value = all_fps
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in fp.definition.items if p.number == num), None
    )
    adapter.get_footprint_pads.side_effect = lambda fp: list(fp.definition.items)
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    return adapter, fps_by_ref


class TestFullPipelineWithTemplates:
    def test_plan_moves_positions_and_angles(self):
        cfg = _build_config()
        pad_pos = (50.0, 50.0)
        ic1 = _make_ic1_fp([
            ("109", *pad_pos, "+1V2_VCCINT"),
            ("62", *pad_pos, "+1V2_VCCINT"),
        ])
        cap_fps = [
            _make_cap_fp("C10", "+1V2_VCCINT", role="LIGHT"),
            _make_cap_fp("C39", "+1V2_VCCINT", role="LIGHT"),
            _make_cap_fp("C35", "+1V2_VCCINT", role="HEAVY"),
            _make_cap_fp("C54", "+1V2_VCCINT", role="HEAVY"),
        ]
        adapter, _ = _make_pool_adapter(ic1, cap_fps)

        planner = PlacementPlanner(adapter, cfg)
        moves = planner.plan_moves()

        assert len(moves) == 4
        by_ref = {m.ref: m for m in moves}
        assert set(by_ref.keys()) == {"C10", "C39", "C35", "C54"}

        def _expected(origin_mm, along, across, rotation_deg):
            ox, oy = origin_mm
            v = rotate_local_offset(along, across, rotation_deg)
            return ox + v.x / MM, oy + v.y / MM

        # Естественный порядок: LIGHT-пул=[C10,C39] -> spoke_109 берёт C10; HEAVY-пул=[C35,C54] -> spoke_109 берёт C35
        ex, ey = _expected((50.0, 50.0), 1.0, -1.0, 90.0)
        assert abs(by_ref["C10"].position.x / MM - ex) < 1e-3
        assert abs(by_ref["C10"].position.y / MM - ey) < 1e-3
        assert by_ref["C10"].angle.degrees == 90.0 + 90.0

        ex2, ey2 = _expected((50.4, 50.0), 1.0, -1.0, 270.0)
        assert abs(by_ref["C39"].position.x / MM - ex2) < 1e-3
        assert abs(by_ref["C39"].position.y / MM - ey2) < 1e-3
        assert by_ref["C39"].angle.degrees == 90.0 + 270.0

    def test_plan_vias_spoke_and_component_level(self):
        cfg = _build_config()
        pad_pos = (50.0, 50.0)
        ic1 = _make_ic1_fp([
            ("109", *pad_pos, "+1V2_VCCINT"),
            ("62", *pad_pos, "+1V2_VCCINT"),
        ])
        cap_fps = [
            _make_cap_fp("C39", "+1V2_VCCINT", role="LIGHT"),
            _make_cap_fp("C54", "+1V2_VCCINT", role="HEAVY"),
            _make_cap_fp("C10", "+1V2_VCCINT", role="LIGHT"),
            _make_cap_fp("C35", "+1V2_VCCINT", role="HEAVY"),
        ]
        net_gnd = Net(name="GND")
        net_power = Net(name="+1V2_VCCINT")

        adapter, fps_by_ref = _make_pool_adapter(ic1, cap_fps)
        adapter.get_net_by_name.side_effect = lambda name: net_gnd if name == "GND" else (
            net_power if name == "+1V2_VCCINT" else None
        )
        adapter.get_bounding_boxes.return_value = []

        planner = PlacementPlanner(adapter, cfg)
        planner.plan_moves()  # заполняет self._planned и self._planned_vias, потребляет пул
        vias = planner.plan_vias()

        spoke_level = [v for v in vias if v.owner_ref == "IC1"]
        component_level = [v for v in vias if v.owner_ref in ("C39", "C54", "C10", "C35")]

        assert len(spoke_level) == 2   # по одной на спицу (109 и 62)
        assert len(component_level) == 4  # по одной на каждый компонент

        for v in spoke_level:
            assert v.net_name == "+1V2_VCCINT"  # net=None в шаблоне -> взят rule.net
        for v in component_level:
            assert v.net_name == "GND"

        # Проверяем, что у всех via есть registry_key (важно для идемпотентности)
        for v in vias:
            assert v.registry_key is not None
            if v.owner_ref == "IC1":
                # via уровня спицы должны содержать SPOKE_LEVEL_ROLE_PLACEHOLDER
                assert SPOKE_LEVEL_ROLE_PLACEHOLDER in v.registry_key
            else:
                # via уровня компонента должны содержать имя роли (HEAVY/LIGHT)
                # В реальности роль будет в ключе, но в этом тесте роль захардкожена в шаблоне
                # Мы можем проверить, что ключ содержит "HEAVY" или "LIGHT"
                assert any(role in v.registry_key for role in ("HEAVY", "LIGHT"))