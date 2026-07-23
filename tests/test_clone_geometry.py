#!/usr/bin/env python3
"""Тесты на geometry/clone_geometry.py — геометрия применения ClonePlacement."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kipy.geometry import Vector2
from kicadspoke.config import ClonePlacement, SpokeTemplate, TemplateVia, TemplateComponentSlot
from kicadspoke.geometry.clone_geometry import apply_clone_geometry
from kicadspoke.exceptions import ValidationError

MM = 1_000_000


def _pi_filter_template() -> SpokeTemplate:
    return SpokeTemplate(
        name="pi_filter",
        vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=-1.0, net="GND")],
        components=[
            TemplateComponentSlot(role="CAP_IN", offset_along_mm=-1.0, offset_across_mm=0.0, angle_deg=0.0),
            TemplateComponentSlot(
                role="CAP_OUT", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=180.0,
                vias=[TemplateVia(offset_along_mm=1.0, offset_across_mm=1.0, net="GND")],
            ),
        ],
    )


class TestApplyCloneGeometry:
    def test_origin_is_direct_no_shift(self):
        clone = ClonePlacement(name="filter1", template="pi_filter", origin_x_mm=50.0, origin_y_mm=50.0)
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10", "CAP_OUT": "C11"})
        assert layout.origin.x == int(50.0 * MM)
        assert layout.origin.y == int(50.0 * MM)

    def test_roles_mapped_and_angle_includes_rotation(self):
        clone = ClonePlacement(name="filter1", template="pi_filter", origin_x_mm=50.0, origin_y_mm=50.0,
                              rotation_deg=90.0)
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10", "CAP_OUT": "C11"})
        cap_in = next(c for c in layout.components if c.role == "CAP_IN")
        cap_out = next(c for c in layout.components if c.role == "CAP_OUT")
        assert cap_in.ref == "C10"
        assert cap_out.ref == "C11"
        # Угол компонента = угол слота + rotation_deg (без mirror)
        assert cap_out.angle_deg == 180.0 + 90.0

    def test_spoke_and_component_level_vias_both_resolved(self):
        clone = ClonePlacement(name="filter1", template="pi_filter", origin_x_mm=0.0, origin_y_mm=0.0)
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10", "CAP_OUT": "C11"})
        assert len(layout.vias) == 1
        assert layout.vias[0].net == "GND"
        cap_out = next(c for c in layout.components if c.role == "CAP_OUT")
        assert len(cap_out.vias) == 1
        assert cap_out.vias[0].net == "GND"

    def test_net_placeholder_resolved_via_params(self):
        tpl = SpokeTemplate(name="dac", vias=[
            TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net="DAC{channel}_DB1")
        ])
        clone = ClonePlacement(name="dac2", template="dac", origin_x_mm=0.0, origin_y_mm=0.0,
                              params={"channel": 2})
        layout = apply_clone_geometry(clone, tpl, {})
        assert layout.vias[0].net == "DAC2_DB1"

    def test_net_overrides_applied(self):
        tpl = SpokeTemplate(name="mcu", vias=[
            TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net="/STM32F4xx/BOOT0")
        ])
        clone = ClonePlacement(name="mcu2", template="mcu", origin_x_mm=0.0, origin_y_mm=0.0,
                              net_overrides={"/STM32F4xx/BOOT0": "/STM32F4xx_2/BOOT0"})
        layout = apply_clone_geometry(clone, tpl, {})
        assert layout.vias[0].net == "/STM32F4xx_2/BOOT0"

    def test_via_without_net_raises_fatal(self):
        """Нет rule_net, на который можно упасть, в отличие от ManualSpoke — via без net фатальна."""
        tpl = SpokeTemplate(name="bad", vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net=None)])
        clone = ClonePlacement(name="x", template="bad", origin_x_mm=0.0, origin_y_mm=0.0)
        with pytest.raises(ValidationError):
            apply_clone_geometry(clone, tpl, {})

    def test_role_without_resolved_ref_is_skipped(self):
        clone = ClonePlacement(name="filter1", template="pi_filter", origin_x_mm=0.0, origin_y_mm=0.0)
        layout = apply_clone_geometry(clone, _pi_filter_template(), {"CAP_IN": "C10"})  # CAP_OUT не разрешена
        assert len(layout.components) == 1
        assert layout.components[0].role == "CAP_IN"

    # ---------- Новые тесты для mirror и anchor_position ----------
    def test_mirror_flips_geometry_and_angle(self):
        """Проверяем, что mirror=True зеркалирует X-координаты и меняет угол по формуле 180−φ."""
        tpl = SpokeTemplate(
            name="simple",
            components=[
                TemplateComponentSlot(role="A", offset_along_mm=1.0, offset_across_mm=0.0, angle_deg=45.0)
            ]
        )
        clone = ClonePlacement(name="mirror_test", template="simple", origin_x_mm=10.0, origin_y_mm=20.0,
                               rotation_deg=30.0)
        role_to_ref = {"A": "C1"}

        # Без mirror
        layout_no = apply_clone_geometry(clone, tpl, role_to_ref, mirror=False)
        comp_no = layout_no.components[0]
        # С mirror
        layout_mirror = apply_clone_geometry(clone, tpl, role_to_ref, mirror=True)
        comp_mirror = layout_mirror.components[0]

        # X-координата зеркалируется относительно origin (10,20)
        origin_x = int(10.0 * MM)
        expected_x = origin_x - (comp_no.position.x - origin_x)  # отражение
        assert comp_mirror.position.x == expected_x
        assert comp_mirror.position.y == comp_no.position.y  # Y не меняется

        # Угол: 180 − (45 + 30) = 105°
        expected_angle = (180.0 - (45.0 + 30.0)) % 360.0
        assert abs(comp_mirror.angle_deg - expected_angle) < 1e-6

    def test_anchor_position_shifts_origin(self):
        """Если задан anchor_position, origin = anchor_position + (origin_x, origin_y) (плоский сдвиг)."""
        tpl = SpokeTemplate(name="single", components=[TemplateComponentSlot(role="A")])
        clone = ClonePlacement(name="anchor_test", template="single",
                               origin_x_mm=5.0, origin_y_mm=7.0)
        anchor = Vector2.from_xy(int(100.0 * MM), int(200.0 * MM))
        layout = apply_clone_geometry(clone, tpl, {"A": "C1"}, anchor_position=anchor)
        # origin должен быть (100+5, 200+7) мм
        assert layout.origin.x == int((100.0 + 5.0) * MM)
        assert layout.origin.y == int((200.0 + 7.0) * MM)