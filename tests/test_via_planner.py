#!/usr/bin/env python3
"""
Task B regression: ViaPlanner keepout must see sibling regular vias planned in
the same plan_vias() run.

Bug (found 2026-07-31): _build_keepout() only considered pad/component bounding
boxes, not the planned_vias list, so a thermal via could land exactly ON TOP of
a regular spoke/component via planned earlier in the same run. The fix adds the
planned vias to the keepout as circular obstacles
(kicadstamp/placement/services/via_planner.py:_build_keepout).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2, Angle
from kipy.board_types import Pad

from kicadstamp.config import Config, ThermalViaArrayConfig
from kicadstamp.placement.commands import ViaCommand
from kicadstamp.placement.services.via_planner import ViaPlanner

MM = 1_000_000


def _make_thermal_pad(number="1", x_mm=0.0, y_mm=0.0, size_mm=4.0):
    """Pad with a size_mm x size_mm copper layer centred at (x_mm, y_mm),
    angle 0. A 1x1 grid puts the single ideal point at the pad centre; the
    copper is large enough for the free-point search around a blocked point."""
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    layer = MagicMock()
    layer.size = Vector2.from_xy(int(size_mm * MM), int(size_mm * MM))
    pad.padstack.copper_layers = [layer]
    pad.padstack.angle = Angle.from_degrees(0.0)
    return pad


def _make_anchor_fp(pad):
    fp = MagicMock()
    fp.reference_field.text.value = "Q1"
    return fp


def _make_adapter(fp, pad):
    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: fp if ref == "Q1" else None
    adapter.get_footprint_pads.return_value = [pad]
    adapter.get_pad_by_number.side_effect = lambda _fp, num: pad if num == "1" else None
    adapter.get_bounding_boxes.return_value = []
    return adapter


def _make_cfg():
    tva = ThermalViaArrayConfig(
        name="q1_thermal",
        pad="1",
        anchor_ref="Q1",
        net="GND",
        rows=1,
        cols=1,
        margin_mm=0.0,
        pattern="grid",
        drill_mm=0.3,
        diameter_mm=0.6,
    )
    return Config(
        layer='B.Cu',
        cells={},
        thermal_via_array=tva,
        rules=[],
        clone_placements=[],
        skip_existing_components=False,
        via_keepout_clearance_mm=0.2,
        via_search_step_mm=0.1,
        via_search_max_radius_mm=5.0,
        via_search_n_directions=8,
    )


def _thermal_vias(vias):
    """Thermal vias are the ones with a registry_key; planned_vias passed in by
    the caller have registry_key=None and are filtered out here."""
    return [v for v in vias if v.registry_key is not None]


class TestViaPlannerPlannedViaKeepout:
    def test_thermal_via_placed_at_ideal_point_when_no_planned_via_blocks(self):
        """Control: with no sibling via, the 1x1 grid thermal via lands exactly
        on the ideal grid point (the pad centre)."""
        pad = _make_thermal_pad()
        fp = _make_anchor_fp(pad)
        adapter = _make_adapter(fp, pad)
        planner = ViaPlanner(adapter, _make_cfg())

        vias = planner.plan_vias(planned_components=[], planned_vias=[])
        thermal = _thermal_vias(vias)

        assert len(thermal) == 1
        # ideal grid point for rows=1, cols=1 is the pad centre (0, 0)
        assert abs(thermal[0].position.x) <= ViaPlanner._VIA_POSITION_TOLERANCE_NM
        assert abs(thermal[0].position.y) <= ViaPlanner._VIA_POSITION_TOLERANCE_NM

    def test_thermal_via_not_placed_on_planned_sibling_via(self):
        """Task B regression: a regular via planned earlier in the same run sits
        exactly at the thermal-grid ideal point. The thermal via must NOT be
        placed on top of it — the keepout built from planned_vias pushes it away
        to a free spot on the same pad."""
        pad = _make_thermal_pad()
        fp = _make_anchor_fp(pad)
        adapter = _make_adapter(fp, pad)
        planner = ViaPlanner(adapter, _make_cfg())

        sibling = ViaCommand(
            position=Vector2.from_xy(0, 0),  # exactly the thermal-grid ideal point
            drill_mm=0.3,
            diameter_mm=0.6,
            net_name="GND",
            owner_ref="Q1",
        )
        vias = planner.plan_vias(planned_components=[], planned_vias=[sibling])
        thermal = _thermal_vias(vias)

        # A free spot exists nearby on the 4x4 mm pad, so the thermal via is
        # still planned — but it must not sit on top of the sibling via.
        assert len(thermal) == 1
        assert abs(thermal[0].position.x) > ViaPlanner._VIA_POSITION_TOLERANCE_NM or \
               abs(thermal[0].position.y) > ViaPlanner._VIA_POSITION_TOLERANCE_NM
