#!/usr/bin/env python3
"""Тесты на загрузку ClonePlacement (config.py, TemplatePlacer)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadspoke.config import load_config

YAML_TEXT = """

templates:
  dac_channel:
    components:
      - role: DAC_IC
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
    vias:
      - offset_along_mm: 1.0
        offset_across_mm: 1.0
        net: "DAC{channel}_DB1"
clone_placements:
  - name: dac_channel_2
    template: dac_channel
    origin_x_mm: 80.0
    origin_y_mm: 40.0
    rotation_deg: 90.0
    params:
      channel: 2
  - name: mcu_section
    template: dac_channel
    origin_x_mm: 0.0
    origin_y_mm: 0.0
    net_overrides:
      "/STM32F4xx/BOOT0": "/STM32F4xx_2/BOOT0"
"""


def test_clone_placements_loaded_with_all_fields(tmp_path):
    config_file = tmp_path / "test.yaml"
    config_file.write_text(YAML_TEXT, encoding="utf-8")

    cfg = load_config(str(config_file))
    assert len(cfg.clone_placements) == 2

    cp1 = cfg.clone_placements[0]
    assert cp1.name == "dac_channel_2"
    assert cp1.template == "dac_channel"
    assert cp1.origin_x_mm == 80.0
    assert cp1.origin_y_mm == 40.0
    assert cp1.rotation_deg == 90.0
    assert cp1.params == {"channel": 2}
    assert cp1.nets == {}
    assert cp1.net_overrides == {}
    assert cp1.enabled is True

    cp2 = cfg.clone_placements[1]
    assert cp2.rotation_deg == 0.0  # дефолт, не указан в YAML
    assert cp2.net_overrides == {"/STM32F4xx/BOOT0": "/STM32F4xx_2/BOOT0"}


def test_no_clone_placements_gives_empty_list(tmp_path):
    config_file = tmp_path / "test2.yaml"
    config_file.write_text("", encoding="utf-8")
    cfg = load_config(str(config_file))
    assert cfg.clone_placements == []
