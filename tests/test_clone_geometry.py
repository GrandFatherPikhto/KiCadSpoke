#!/usr/bin/env python3
"""Тесты на geometry/clone_geometry.py — геометрия применения ClonePlacement."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadspoke.config import ClonePlacement, SpokeTemplate, TemplateVia, TemplateComponentSlot
from kicadspoke.geometry.clone_geometry import apply_clone_geometry
from kicadspoke.exceptions import ValidationError

MM = 1_000_000


def _pi_filter_template():
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
