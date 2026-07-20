#!/usr/bin/env python3
"""
Тесты на skip_existing_components — поле было объявлено в config.py, но
нигде не использовалось (тот же класс находки, что раньше был с
socket_path/place_components). Теперь: компонент, уже стоящий на целевой
позиции/угле/слое, не перемещается повторно; via, уже существующая в
той же точке на той же цепи, не создаётся повторно.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2, Angle
from kipy.board_types import BoardLayer, Pad, Net

from kicadspoke.config import (
    Config, ThermalViaArrayConfig, ManualSpoke, SpokeTemplate,
    TemplateComponentSlot, Rule
)
from kicadspoke.placement.planner import PlacementPlanner
from kicadspoke.placement.services.via_planner import ViaPlanner

MM = 1_000_000


def _make_pad(number, x_mm, y_mm, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net.name = net_name
    return pad


class TestSkipExistingComponents:
    def _cfg(self, skip: bool):
        template = SpokeTemplate(
            name="t",
            components=[TemplateComponentSlot(role="SOLO", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=0.0)],
        )
        spoke = ManualSpoke(pad="17", template="t", rotation_deg=0.0)
        return Config(
            layer='B.Cu',
            templates={"t": template},
            thermal_via_array=ThermalViaArrayConfig(enabled=False),
            rules=[Rule(net="+3V3", anchor_ref='IC1', spokes=[spoke])],
            skip_existing_components=skip,
        )

    def _ic1(self):
        fp = MagicMock()
        fp.reference_field.text.value = "IC1"
        fp.definition.items = [_make_pad("17", 50.0, 50.0, "+3V3")]
        return fp

    def _setup_adapter(self, ic1, c5):
        """C5 помечен ролью SOLO и сидит на цепи +3V3 -- пул его находит."""
        c5.reference_field.text.value = "C5"
        c5_pad = MagicMock(); c5_pad.net.name = "+3V3"
        c5.definition.items = [c5_pad]

        adapter = MagicMock()
        adapter.get_footprint.side_effect = lambda ref: ic1 if ref == "IC1" else (c5 if ref == "C5" else None)
        adapter.get_footprints.return_value = [ic1, c5]
        adapter.get_pad_by_number.side_effect = lambda fp, num: next(
            (p for p in fp.definition.items if p.number == num), None
        )
        adapter.get_footprint_pads.side_effect = lambda fp: list(fp.definition.items)
        adapter.get_field_value.side_effect = lambda fp, name: "SOLO" if fp is c5 else None
        return adapter

    def test_skips_move_when_component_already_in_place(self):
        cfg = self._cfg(skip=True)
        ic1 = self._ic1()

        # C5 УЖЕ ровно там, где его хочет видеть шаблон: (51.0, 50.0), угол 0°, B.Cu
        c5 = MagicMock()
        c5.position = Vector2.from_xy(int(51.0 * MM), int(50.0 * MM))
        c5.orientation = Angle.from_degrees(0.0)
        c5.layer = BoardLayer.BL_B_Cu

        adapter = self._setup_adapter(ic1, c5)
        planner = PlacementPlanner(adapter, cfg)
        moves = planner.plan_moves()
        assert len(moves) == 0, "компонент уже на месте -- перемещение не должно планироваться"

    def test_does_not_skip_when_flag_disabled(self):
        cfg = self._cfg(skip=False)
        ic1 = self._ic1()
        c5 = MagicMock()
        c5.position = Vector2.from_xy(int(51.0 * MM), int(50.0 * MM))
        c5.orientation = Angle.from_degrees(0.0)
        c5.layer = BoardLayer.BL_B_Cu

        adapter = self._setup_adapter(ic1, c5)
        planner = PlacementPlanner(adapter, cfg)
        moves = planner.plan_moves()
        assert len(moves) == 1, "skip_existing_components=False -- перемещение должно планироваться как обычно"

    def test_does_not_skip_when_position_differs(self):
        cfg = self._cfg(skip=True)
        ic1 = self._ic1()
        # C5 НЕ на целевой позиции -- реально в другом месте
        c5 = MagicMock()
        c5.position = Vector2.from_xy(0, 0)
        c5.orientation = Angle.from_degrees(0.0)
        c5.layer = BoardLayer.BL_B_Cu

        adapter = self._setup_adapter(ic1, c5)
        planner = PlacementPlanner(adapter, cfg)
        moves = planner.plan_moves()
        assert len(moves) == 1, "компонент НЕ на целевой позиции -- перемещение должно планироваться"


class TestSkipExistingVias:
    def _cfg(self, skip: bool):
        return Config(
            layer='B.Cu',
            templates={},
            thermal_via_array=ThermalViaArrayConfig(enabled=False),
            rules=[],
            skip_existing_components=skip,
        )

    def test_skips_gnd_via_already_present(self):
        cfg = self._cfg(skip=True)
        planner_via = ViaPlanner(MagicMock(), cfg)

        existing_via = MagicMock()
        existing_via.net.name = "GND"
        existing_via.position = Vector2.from_xy(int(51.0 * MM), int(50.0 * MM))

        target_pos = Vector2.from_xy(int(51.001 * MM), int(50.0 * MM))  # 1 микрон разницы -- в допуске
        assert planner_via._via_already_exists([existing_via], target_pos, "GND") is True

    def test_does_not_match_different_net(self):
        cfg = self._cfg(skip=True)
        planner_via = ViaPlanner(MagicMock(), cfg)

        existing_via = MagicMock()
        existing_via.net.name = "+3V3"
        existing_via.position = Vector2.from_xy(int(51.0 * MM), int(50.0 * MM))

        target_pos = Vector2.from_xy(int(51.0 * MM), int(50.0 * MM))
        assert planner_via._via_already_exists([existing_via], target_pos, "GND") is False

    def test_does_not_match_far_position(self):
        cfg = self._cfg(skip=True)
        planner_via = ViaPlanner(MagicMock(), cfg)

        existing_via = MagicMock()
        existing_via.net.name = "GND"
        existing_via.position = Vector2.from_xy(int(51.0 * MM), int(50.0 * MM))

        target_pos = Vector2.from_xy(int(55.0 * MM), int(50.0 * MM))  # далеко
        assert planner_via._via_already_exists([existing_via], target_pos, "GND") is False
