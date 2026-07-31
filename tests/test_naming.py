#!/usr/bin/env python3
"""Tests for name/--only identity.

ThermalViaArrayConfig.name/ClonePlacement.name — REQUIRED in YAML (the loader
fatals if missing), no fallback to thermal_<pad>/'?'.

Rule.name — OPTIONAL: falls back to net (rule_effective_name), since net is
not fit to be a grouping label (Cluster exists for that), but is perfectly
fine as the identity of a SINGLE rule when no explicit name is given. The
loader fatals if two rules resolve to the same effective identity (see
config/loader.py) — not a silent pick of one over the other.

See --only in kicadstamp_cli.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import (
    Rule, ThermalViaArrayConfig,
    rule_effective_name, thermal_via_array_effective_name, load_config,
)
from kicadstamp.exceptions import ValidationError


class TestEffectiveNameAccessors:
    """rule_effective_name/thermal_via_array_effective_name — just .name
    for ThermalViaArrayConfig (the loader guarantees it's set for anything
    actually loaded from YAML); for Rule, .name or a fallback to .net."""

    def test_rule_effective_name_is_plain_name(self):
        rule = Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA", name="fpga_3v3_bank")
        assert rule_effective_name(rule) == "fpga_3v3_bank"

    def test_rule_effective_name_falls_back_to_net(self):
        rule = Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA")
        assert rule.name is None
        assert rule_effective_name(rule) == "+3V3_VCCIO"

    def test_thermal_effective_name_is_plain_name(self):
        tva = ThermalViaArrayConfig(retired=False, anchor_role="FPGA", pad="145", name="fpga_thermal")
        assert thermal_via_array_effective_name(tva) == "fpga_thermal"


def test_thermal_via_array_retired_defaults_false():
    """Regression for Task A.1: constructed directly in Python (bypassing the
    YAML loader), ThermalViaArrayConfig's 'retired' field defaults to False —
    unified with Rule/ManualSpoke/ClonePlacement. This is ONLY about the bare
    dataclass constructor (e.g. tests building one by hand); it says nothing
    about what load_config() should default to for an ABSENT thermal_via_array:
    section — see test_thermal_via_array_absent_section_stays_retired below,
    found 2026-07-31: naively reusing this same False default in the loader for
    an absent section made every config without thermal_via_array fatal on
    apply (no anchor_ref/anchor_role either, since those also default to None)."""
    assert ThermalViaArrayConfig().retired is False


def test_thermal_via_array_absent_section_stays_retired(tmp_path):
    """Regression 2026-07-31: a config with NO thermal_via_array: section at all
    must load with retired=True (does nothing), exactly like before the
    active/enabled -> skip/retired rename — NOT retired=False (which would make
    ViaPlanner._resolve_thermal_anchor() raise ValidationError on every apply,
    since anchor_ref/anchor_role are also None when nothing was configured)."""
    config_file = tmp_path / "test.yaml"
    config_file.write_text("layer: B.Cu\nrules: []\ncells: {}\n", encoding="utf-8")
    cfg, _ = load_config(str(config_file))

    assert cfg.thermal_via_array.retired is True


YAML_TEXT = """
layer: B.Cu
thermal_via_array:
  retired: false
  anchor_role: FPGA
  pad: '145'
  name: fpga_thermal

rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  name: fpga_3v3_bank
  spokes:
  - pad: '17'
    cell: cap_pair_standard

cells:
  cap_pair_standard:
    components: []
    vias: []
"""


class TestNameLoadedFromYaml:
    """name: actually reaches Rule/ThermalViaArrayConfig from YAML, not just
    accepted by the dataclass constructor (regression check on loader.py)."""

    def test_rule_name_loaded(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text(YAML_TEXT, encoding="utf-8")
        cfg, _ = load_config(str(config_file))

        rule = cfg.rules[0]
        assert rule.name == "fpga_3v3_bank"
        assert rule_effective_name(rule) == "fpga_3v3_bank"

    def test_thermal_via_array_name_loaded(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text(YAML_TEXT, encoding="utf-8")
        cfg, _ = load_config(str(config_file))

        assert cfg.thermal_via_array.name == "fpga_thermal"
        assert thermal_via_array_effective_name(cfg.thermal_via_array) == "fpga_thermal"


class TestNameRequired:
    """Without name: — fatal, not a silent fallback/'?'. Two remaining
    places (Rule is the exception now, see TestRuleNameOptional below):
    thermal_via_array (only when the section is actually present),
    clone_placement (closes an old hole with a silent '?')."""

    def test_thermal_via_array_without_name_is_fatal(self, tmp_path):
        text = """
layer: B.Cu
thermal_via_array:
  retired: false
  anchor_role: FPGA
  pad: '145'
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(str(config_file))

    def test_absent_thermal_via_array_section_is_not_fatal(self, tmp_path):
        """Section absent from YAML entirely — not the same as "present but
        without name" — nothing is being named here, no error. It does default
        to retired=True though (found 2026-07-31: a retired=False default here
        would make ViaPlanner try to resolve a thermal anchor with no
        anchor_ref/anchor_role set and raise ValidationError on every apply —
        see test_thermal_via_array_absent_section_stays_retired above)."""
        text = """
layer: B.Cu
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.thermal_via_array.retired is True
        assert cfg.thermal_via_array.name is None

    def test_clone_placement_without_name_is_fatal(self, tmp_path):
        text = """
layer: B.Cu
clone_placements:
- role: SOMETHING
  xy: [0.0, 0.0]
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(str(config_file))


class TestRuleNameOptional:
    """Rule.name — the one exception from TestNameRequired: optional, net
    is a working fallback for the identity of a SINGLE rule (not a grouping
    mechanism)."""

    def test_rule_without_name_loads_fine(self, tmp_path):
        text = """
layer: B.Cu
rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  spokes: []
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        rule = cfg.rules[0]
        assert rule.name is None
        assert rule_effective_name(rule) == "+3V3_VCCIO"

    def test_two_rules_same_net_without_name_is_fatal(self, tmp_path):
        """Two anchors (e.g. two different ICs) on the same GND net without
        a distinguishing name: — an --only identity collision, must be
        caught at load time, not silently resolved in favour of either one."""
        text = """
layer: B.Cu
rules:
- net: GND
  anchor_role: FPGA
  spokes: []
- net: GND
  anchor_role: GD32F470
  spokes: []
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(str(config_file))

    def test_two_rules_same_net_with_distinguishing_name_is_ok(self, tmp_path):
        text = """
layer: B.Cu
rules:
- net: GND
  anchor_role: FPGA
  name: fpga_gnd
  spokes: []
- net: GND
  anchor_role: GD32F470
  spokes: []
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert rule_effective_name(cfg.rules[0]) == "fpga_gnd"
        assert rule_effective_name(cfg.rules[1]) == "GND"


class TestRuleRetired:
    """Rule.retired — symmetric with ManualSpoke.retired/ClonePlacement.retired/
    ThermalViaArrayConfig.retired, default False."""

    def test_default_is_not_retired(self, tmp_path):
        text = """
layer: B.Cu
rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  spokes: []
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.rules[0].retired is False

    def test_retired_true_loaded_from_yaml(self, tmp_path):
        text = """
layer: B.Cu
rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  retired: true
  spokes: []
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.rules[0].retired is True


class TestRuleSkip:
    """Rule.skip / ManualSpoke.skip — orthogonal to retired (default False),
    the inline per-item counterpart of --only/--cluster (see
    drop_inactive_items in kicadstamp_cli.py, added 2026-07-29)."""

    def test_default_is_not_skip(self, tmp_path):
        text = """
layer: B.Cu
rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  spokes:
  - pad: '17'
    cell: t
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.rules[0].skip is False
        assert cfg.rules[0].spokes[0].skip is False

    def test_skip_true_loaded_from_yaml_on_rule_and_spoke(self, tmp_path):
        text = """
layer: B.Cu
rules:
- net: +3V3_VCCIO
  anchor_role: FPGA
  skip: true
  spokes:
  - pad: '17'
    cell: t
    skip: true
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.rules[0].skip is True
        assert cfg.rules[0].spokes[0].skip is True

    def test_thermal_via_array_skip_true_loaded_from_yaml(self, tmp_path):
        text = """
layer: B.Cu
thermal_via_array:
  retired: false
  anchor_role: FPGA
  pad: '145'
  name: fpga_thermal
  skip: true
cells: {}
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(config_file))
        assert cfg.thermal_via_array.skip is True
