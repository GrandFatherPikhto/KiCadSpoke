#!/usr/bin/env python3
"""check_unknown_keys coverage for the two loading paths that were missing it
until now: ManualSpoke (spokes: inside a rule) and thermal_via_array:. Same
bug class as origin-by-via-net (dash typo) hit live on boards/3ch-awg-tia —
a typo'd/wrong field name in these two blocks was previously silently
ignored, no error at all. See _MANUAL_SPOKE_KNOWN_KEYS/
_THERMAL_VIA_ARRAY_KNOWN_KEYS in config/loader.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadspoke.config import load_config
from kicadspoke.exceptions import ValidationError


class TestManualSpokeUnknownKeys:
    def test_typo_field_in_spoke_is_fatal(self, tmp_path):
        text = """
layer: B.Cu
rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  spokes:
  - pad: '17'
    template: t
    enalbed: false
templates: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError, match="enalbed"):
            load_config(str(config_file))

    def test_suggests_close_match(self, tmp_path):
        text = """
layer: B.Cu
rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  spokes:
  - pad: '17'
    template: t
    enalbed: false
templates: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError, match="enabled"):
            load_config(str(config_file))

    def test_all_known_spoke_fields_load_fine(self, tmp_path):
        text = """
layer: B.Cu
rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  spokes:
  - pad: '17'
    template: t
    shift_x_mm: 1.0
    shift_y_mm: -1.0
    rotation_deg: 90.0
    enabled: true
    cluster: Channel_0
    active: false
templates: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        spoke = cfg.rules[0].spokes[0]
        assert spoke.cluster == "Channel_0"
        assert spoke.active is False


class TestThermalViaArrayUnknownKeys:
    def test_typo_field_is_fatal(self, tmp_path):
        text = """
layer: B.Cu
thermal_via_array:
  enabled: true
  anchor_role: FPGA
  pad: '145'
  name: fpga_thermal
  rowss: 4
templates: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError, match="rowss"):
            load_config(str(config_file))

    def test_all_known_fields_load_fine(self, tmp_path):
        text = """
layer: B.Cu
thermal_via_array:
  enabled: true
  anchor_role: FPGA
  anchor_sheet: Channel_0
  anchor_cluster: FPGA_BANK
  pad: '145'
  net: GND
  rows: 4
  cols: 4
  margin_mm: 0.5
  pattern: grid
  drill_mm: 0.3
  diameter_mm: 0.5
  name: fpga_thermal
  active: false
templates: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.thermal_via_array.active is False

    def test_absent_thermal_via_array_is_fine(self, tmp_path):
        text = """
layer: B.Cu
templates: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.thermal_via_array.enabled is False
