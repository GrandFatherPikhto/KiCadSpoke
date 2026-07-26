#!/usr/bin/env python3
"""Тесты на Rule.name/ThermalViaArrayConfig.name и их фоллбэк-резолв
(rule_effective_name/thermal_via_array_effective_name) — см. --only в
kicadspoke_cli.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadspoke.config import (
    Rule, ThermalViaArrayConfig, ManualSpoke,
    rule_effective_name, thermal_via_array_effective_name, load_config,
)


class TestRuleEffectiveName:
    def test_explicit_name_wins(self):
        rule = Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA", name="fpga_3v3_bank")
        assert rule_effective_name(rule) == "fpga_3v3_bank"

    def test_falls_back_to_net_without_name(self):
        rule = Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA")
        assert rule_effective_name(rule) == "+3V3_VCCIO"


class TestThermalViaArrayEffectiveName:
    def test_explicit_name_wins(self):
        tva = ThermalViaArrayConfig(enabled=True, anchor_role="FPGA", pad="145", name="fpga_thermal")
        assert thermal_via_array_effective_name(tva) == "fpga_thermal"

    def test_falls_back_to_thermal_pad_without_name(self):
        tva = ThermalViaArrayConfig(enabled=True, anchor_role="FPGA", pad="145")
        assert thermal_via_array_effective_name(tva) == "thermal_145"


YAML_TEXT = """
layer: B.Cu
thermal_via_array:
  enabled: true
  anchor_role: FPGA
  pad: '145'
  name: fpga_thermal

rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  name: fpga_3v3_bank
  spokes:
  - pad: '17'
    template: cap_pair_standard
- net: +1V2_VCCINT
  anchor_role: FPGA
  spokes:
  - pad: '5'
    template: cap_pair_standard

templates:
  cap_pair_standard:
    components: []
    vias: []
"""


class TestNameLoadedFromYaml:
    """name: реально доходит из YAML до Rule/ThermalViaArrayConfig, а не
    только принимается конструктором dataclass (регрессия на loader.py)."""

    def test_rule_name_and_fallback_both_loaded(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text(YAML_TEXT, encoding="utf-8")
        cfg = load_config(str(config_file))

        by_net = {r.net: r for r in cfg.rules}
        assert by_net["+3V3_VCCIO"].name == "fpga_3v3_bank"
        assert rule_effective_name(by_net["+3V3_VCCIO"]) == "fpga_3v3_bank"

        # второе правило без явного name: — фоллбэк на net
        assert by_net["+1V2_VCCINT"].name is None
        assert rule_effective_name(by_net["+1V2_VCCINT"]) == "+1V2_VCCINT"

    def test_thermal_via_array_name_loaded(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text(YAML_TEXT, encoding="utf-8")
        cfg = load_config(str(config_file))

        assert cfg.thermal_via_array.name == "fpga_thermal"
        assert thermal_via_array_effective_name(cfg.thermal_via_array) == "fpga_thermal"
