#!/usr/bin/env python3
"""Tests for kicadspoke/config/includes.py — generic `include:` for splitting
a profile YAML into subsystem files (extract_profiles + clone_placements +
rules + templates together, unlike per-section *_file keys)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadspoke.config import load_config
from kicadspoke.exceptions import ValidationError

MINIMAL_TEMPLATE = """
templates:
  one_role:
    components:
      - role: THE_ROLE
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
"""


def test_include_merges_clone_placements_and_rules(tmp_path):
    (tmp_path / "sub.yaml").write_text(MINIMAL_TEMPLATE + """
clone_placements:
  - name: from_sub
    template: one_role
    origin_x_mm: 1.0
    origin_y_mm: 2.0
""", encoding="utf-8")

    root = tmp_path / "root.yaml"
    root.write_text("""
include:
  - sub.yaml
clone_placements:
  - name: from_root
    template: one_role
    origin_x_mm: 0.0
    origin_y_mm: 0.0
""", encoding="utf-8")

    cfg = load_config(str(root))
    names = {cp.name for cp in cfg.clone_placements}
    assert names == {"from_root", "from_sub"}
    assert "one_role" in cfg.templates


def test_include_templates_merge_alongside_templates_file(tmp_path):
    (tmp_path / "sub.yaml").write_text("""
templates:
  from_include:
    components:
      - role: R1
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
""", encoding="utf-8")
    (tmp_path / "ext_templates.yaml").write_text("""
from_templates_file:
  components:
    - role: R2
      offset_along_mm: 0.0
      offset_across_mm: 0.0
      angle_deg: 0.0
""", encoding="utf-8")

    root = tmp_path / "root.yaml"
    root.write_text("""
templates_file: ext_templates.yaml
include:
  - sub.yaml
""", encoding="utf-8")

    cfg = load_config(str(root))
    assert set(cfg.templates.keys()) == {"from_include", "from_templates_file"}


def test_duplicate_template_key_across_includes_is_fatal(tmp_path):
    (tmp_path / "a.yaml").write_text("""
templates:
  dup:
    components:
      - role: R1
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
""", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("""
templates:
  dup:
    components:
      - role: R2
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
""", encoding="utf-8")

    root = tmp_path / "root.yaml"
    root.write_text("""
include:
  - a.yaml
  - b.yaml
""", encoding="utf-8")

    with pytest.raises(ValidationError, match="duplicate"):
        load_config(str(root))


def test_disabled_include_is_skipped_before_existence_check(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("""
include:
  - path: does_not_exist.yaml
    enabled: false
""" + MINIMAL_TEMPLATE, encoding="utf-8")

    cfg = load_config(str(root))
    assert cfg.clone_placements == []


def test_cycle_is_fatal(tmp_path):
    (tmp_path / "a.yaml").write_text("include:\n  - b.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("include:\n  - a.yaml\n", encoding="utf-8")

    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - a.yaml\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="included more than once"):
        load_config(str(root))


def test_diamond_reinclude_is_fatal(tmp_path):
    (tmp_path / "d.yaml").write_text(MINIMAL_TEMPLATE, encoding="utf-8")
    (tmp_path / "b.yaml").write_text("include:\n  - d.yaml\n", encoding="utf-8")
    (tmp_path / "c.yaml").write_text("include:\n  - d.yaml\n", encoding="utf-8")

    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - b.yaml\n  - c.yaml\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="included more than once"):
        load_config(str(root))


def test_dict_section_used_as_list_is_fatal(tmp_path):
    """Real mistake hit live: extract_profiles: (a mapping) accidentally
    renamed to clone_placements: (a list section) — YAML still parses (dict
    of dicts), but list(dict) silently gives back its KEYS as bare strings,
    which used to blow up downstream with a confusing AttributeError instead
    of a clear fatal here."""
    (tmp_path / "sub.yaml").write_text("""
clone_placements:
  some_profile:
    params:
      PWR_IN: '+5V'
""", encoding="utf-8")

    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="must be a list"):
        load_config(str(root))


def test_bare_list_at_top_level_is_fatal(tmp_path):
    """Real mistake hit live: list items pasted without their wrapping
    'clone_placements:' key — file's top level is a bare YAML list."""
    (tmp_path / "sub.yaml").write_text("""
- name: stray
  template: one_role
  origin_x_mm: 0.0
  origin_y_mm: 0.0
""", encoding="utf-8")

    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="must be a YAML mapping"):
        load_config(str(root))


def test_nested_include_is_merged(tmp_path):
    (tmp_path / "c.yaml").write_text(MINIMAL_TEMPLATE + """
clone_placements:
  - name: from_c
    template: one_role
    origin_x_mm: 0.0
    origin_y_mm: 0.0
""", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("include:\n  - c.yaml\n", encoding="utf-8")

    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - b.yaml\n", encoding="utf-8")

    cfg = load_config(str(root))
    assert [cp.name for cp in cfg.clone_placements] == ["from_c"]
