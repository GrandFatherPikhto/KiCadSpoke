#!/usr/bin/env python3
"""Тесты на загрузку ClonePlacement (config.py, TemplatePlacer)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadspoke.config import load_config
from kicadspoke.exceptions import ValidationError

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


# ---------- Новые тесты на поля ClonePlacement ----------
def test_anchor_ref_without_origin(tmp_path):
    """Якорный режим: указываем anchor_ref, origin_x/y становятся опциональным сдвигом."""
    yaml_content = """
templates:
  t:
    components: []
clone_placements:
  - name: anchored
    template: t
    anchor_ref: IC1
    anchor_pad: 17
    origin_x_mm: 2.5
    origin_y_mm: 3.7
"""
    config_file = tmp_path / "anchor.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.anchor_ref == "IC1"
    assert cp.anchor_pad == "17"
    assert cp.origin_x_mm == 2.5
    assert cp.origin_y_mm == 3.7
    # origin_x/y обязательны? Нет, они опциональны, но если не указаны, будут 0.0 по умолчанию.
    # Проверим случай без origin_x/y:

def test_anchor_ref_without_origin_uses_default_zero(tmp_path):
    yaml_content = """
templates:
  t:
    components: []
clone_placements:
  - name: anchored
    template: t
    anchor_ref: IC1
"""
    config_file = tmp_path / "anchor_no_origin.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.origin_x_mm == 0.0
    assert cp.origin_y_mm == 0.0


def test_anchor_role_with_anchor_sheet(tmp_path):
    """Якорь по роли + сужение по листу."""
    yaml_content = """
templates:
  t:
    components: []
clone_placements:
  - name: by_role
    template: t
    anchor_role: MCU
    anchor_sheet: Channel_0
    anchor_pad: 17
"""
    config_file = tmp_path / "anchor_role.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.anchor_role == "MCU"
    assert cp.anchor_sheet == "Channel_0"
    assert cp.anchor_pad == "17"
    assert cp.origin_x_mm == 0.0
    assert cp.origin_y_mm == 0.0


def test_layer_and_mirror(tmp_path):
    """Слой и зеркало."""
    yaml_content = """
templates:
  t:
    layer: F.Cu
    components: []
clone_placements:
  - name: mirrored
    template: t
    origin_x_mm: 0
    origin_y_mm: 0
    layer: B.Cu
    mirror: true
"""
    config_file = tmp_path / "layer_mirror.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.layer == "B.Cu"
    assert cp.mirror is True


def test_nets_and_refs(tmp_path):
    """Явные nets и refs."""
    yaml_content = """
templates:
  t:
    components:
      - role: A
      - role: B
clone_placements:
  - name: with_nets
    template: t
    origin_x_mm: 0
    origin_y_mm: 0
    nets:
      A: GND
      B: VCC
    refs:
      A: C1
"""
    config_file = tmp_path / "nets_refs.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.nets == {"A": "GND", "B": "VCC"}
    assert cp.refs == {"A": "C1"}


def test_by_selection_flag(tmp_path):
    """Явный режим 'по выделению'."""
    yaml_content = """
templates:
  t:
    components: []
clone_placements:
  - name: selection_mode
    template: t
    origin_x_mm: 0
    origin_y_mm: 0
    by_selection: true
"""
    config_file = tmp_path / "by_selection.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    cfg = load_config(str(config_file))
    cp = cfg.clone_placements[0]
    assert cp.by_selection is True


def test_by_selection_and_nets_conflict_raises(tmp_path):
    """by_selection: true + nets должны вызвать ValidationError."""
    yaml_content = """
templates:
  t:
    components:
      - role: A
clone_placements:
  - name: conflict
    template: t
    origin_x_mm: 0
    origin_y_mm: 0
    by_selection: true
    nets:
      A: GND
"""
    config_file = tmp_path / "conflict.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="by_selection.*nets"):
        load_config(str(config_file))


def test_anchor_ref_and_anchor_role_together_raises(tmp_path):
    """Взаимоисключающие anchor_ref и anchor_role."""
    yaml_content = """
templates:
  t:
    components: []
clone_placements:
  - name: both_anchors
    template: t
    anchor_ref: IC1
    anchor_role: MCU
"""
    config_file = tmp_path / "both_anchors.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="anchor_ref.*anchor_role"):
        load_config(str(config_file))


def test_anchor_sheet_without_anchor_role_raises(tmp_path):
    """anchor_sheet без anchor_role недопустим."""
    yaml_content = """
templates:
  t:
    components: []
clone_placements:
  - name: sheet_without_role
    template: t
    anchor_sheet: Channel_0
"""
    config_file = tmp_path / "sheet_no_role.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="anchor_sheet без anchor_role"):
        load_config(str(config_file))


def test_anchor_pad_without_anchor_ref_or_role_raises(tmp_path):
    """anchor_pad требует anchor_ref или anchor_role."""
    yaml_content = """
templates:
  t:
    components: []
clone_placements:
  - name: pad_without_anchor
    template: t
    anchor_pad: 17
"""
    config_file = tmp_path / "pad_no_anchor.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="anchor_pad без anchor_ref/anchor_role"):
        load_config(str(config_file))


def test_no_anchor_and_no_origin_raises(tmp_path):
    """Если нет якоря, должны быть origin_x/y."""
    yaml_content = """
templates:
  t:
    components: []
clone_placements:
  - name: no_anchor_no_origin
    template: t
"""
    config_file = tmp_path / "no_anchor_no_origin.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValidationError, match="нет ни якоря, ни абсолютных координат"):
        load_config(str(config_file))