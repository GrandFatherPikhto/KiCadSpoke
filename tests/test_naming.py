#!/usr/bin/env python3
"""Тесты на Rule.name/ThermalViaArrayConfig.name/ClonePlacement.name —
ОБЯЗАТЕЛЬНЫ в YAML (загрузчик фатально падает, если отсутствуют), без
фоллбэка на net/thermal_<pad>/'?'. См. --only в kicadspoke_cli.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadspoke.config import (
    Rule, ThermalViaArrayConfig,
    rule_effective_name, thermal_via_array_effective_name, load_config,
)
from kicadspoke.exceptions import ValidationError


class TestEffectiveNameAccessors:
    """rule_effective_name/thermal_via_array_effective_name — просто
    .name (загрузчик гарантирует его наличие для реально загруженных из
    YAML объектов); тут только проверяем, что это не тайный фоллбэк."""

    def test_rule_effective_name_is_plain_name(self):
        rule = Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA", name="fpga_3v3_bank")
        assert rule_effective_name(rule) == "fpga_3v3_bank"

    def test_thermal_effective_name_is_plain_name(self):
        tva = ThermalViaArrayConfig(enabled=True, anchor_role="FPGA", pad="145", name="fpga_thermal")
        assert thermal_via_array_effective_name(tva) == "fpga_thermal"


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

templates:
  cap_pair_standard:
    components: []
    vias: []
"""


class TestNameLoadedFromYaml:
    """name: реально доходит из YAML до Rule/ThermalViaArrayConfig, а не
    только принимается конструктором dataclass (регрессия на loader.py)."""

    def test_rule_name_loaded(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text(YAML_TEXT, encoding="utf-8")
        cfg = load_config(str(config_file))

        rule = cfg.rules[0]
        assert rule.name == "fpga_3v3_bank"
        assert rule_effective_name(rule) == "fpga_3v3_bank"

    def test_thermal_via_array_name_loaded(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text(YAML_TEXT, encoding="utf-8")
        cfg = load_config(str(config_file))

        assert cfg.thermal_via_array.name == "fpga_thermal"
        assert thermal_via_array_effective_name(cfg.thermal_via_array) == "fpga_thermal"


class TestNameRequired:
    """Без name: — фатал, а не тихий фоллбэк/'?'. Три места, три теста —
    rule, thermal_via_array (только когда секция вообще присутствует),
    clone_placement (закрыта старая дыра с молчаливым '?')."""

    def test_rule_without_name_is_fatal(self, tmp_path):
        text = """
layer: B.Cu
rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  spokes: []
templates: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(str(config_file))

    def test_thermal_via_array_without_name_is_fatal(self, tmp_path):
        text = """
layer: B.Cu
thermal_via_array:
  enabled: true
  anchor_role: FPGA
  pad: '145'
templates: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(str(config_file))

    def test_absent_thermal_via_array_section_is_not_fatal(self, tmp_path):
        """Секции вообще нет в YAML — не то же самое, что "есть, но без
        name" — тут ничего не именуем, дефолт (disabled) без ошибок."""
        text = """
layer: B.Cu
templates: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg = load_config(str(config_file))
        assert cfg.thermal_via_array.enabled is False
        assert cfg.thermal_via_array.name is None

    def test_clone_placement_without_name_is_fatal(self, tmp_path):
        text = """
layer: B.Cu
clone_placements:
- role: SOMETHING
  origin_x_mm: 0.0
  origin_y_mm: 0.0
templates: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(str(config_file))
