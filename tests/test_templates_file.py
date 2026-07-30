#!/usr/bin/env python3
"""Tests for templates_file/template_files (kicadspoke/config/loader.py) —
external raw-shaped ({name: {...}}, no 'templates:' wrapper) template files,
independent of include: (see test_config_includes.py for that mechanism)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadspoke.config import load_config
from kicadspoke.exceptions import ValidationError

ONE_ROLE = """
{name}:
  components:
    - role: {role}
      offset_along_mm: 0.0
      offset_across_mm: 0.0
      angle_deg: 0.0
"""


def test_single_templates_file_still_works(tmp_path):
    (tmp_path / "ext.yaml").write_text(ONE_ROLE.format(name="tpl_a", role="R1"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("templates_file: ext.yaml\n", encoding="utf-8")

    cfg, _ = load_config(str(root))
    assert set(cfg.templates.keys()) == {"tpl_a"}


def test_multiple_template_files_merge(tmp_path):
    (tmp_path / "a.yaml").write_text(ONE_ROLE.format(name="tpl_a", role="R1"), encoding="utf-8")
    (tmp_path / "b.yaml").write_text(ONE_ROLE.format(name="tpl_b", role="R2"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("""
template_files:
  - a.yaml
  - b.yaml
""", encoding="utf-8")

    cfg, _ = load_config(str(root))
    assert set(cfg.templates.keys()) == {"tpl_a", "tpl_b"}


def test_templates_file_and_template_files_combined(tmp_path):
    (tmp_path / "single.yaml").write_text(ONE_ROLE.format(name="tpl_single", role="R1"), encoding="utf-8")
    (tmp_path / "a.yaml").write_text(ONE_ROLE.format(name="tpl_a", role="R2"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("""
templates_file: single.yaml
template_files:
  - a.yaml
""", encoding="utf-8")

    cfg, _ = load_config(str(root))
    assert set(cfg.templates.keys()) == {"tpl_single", "tpl_a"}


def test_duplicate_name_across_template_files_is_fatal(tmp_path):
    (tmp_path / "a.yaml").write_text(ONE_ROLE.format(name="dup", role="R1"), encoding="utf-8")
    (tmp_path / "b.yaml").write_text(ONE_ROLE.format(name="dup", role="R2"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("""
template_files:
  - a.yaml
  - b.yaml
""", encoding="utf-8")

    with pytest.raises(ValidationError, match="duplicate template 'dup'"):
        load_config(str(root))


def test_duplicate_name_between_templates_file_and_template_files_is_fatal(tmp_path):
    (tmp_path / "single.yaml").write_text(ONE_ROLE.format(name="dup", role="R1"), encoding="utf-8")
    (tmp_path / "a.yaml").write_text(ONE_ROLE.format(name="dup", role="R2"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("""
templates_file: single.yaml
template_files:
  - a.yaml
""", encoding="utf-8")

    with pytest.raises(ValidationError, match="duplicate template 'dup'"):
        load_config(str(root))


def test_inline_templates_overrides_external(tmp_path):
    (tmp_path / "a.yaml").write_text(ONE_ROLE.format(name="tpl_a", role="EXTERNAL_ROLE"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("""
template_files:
  - a.yaml
templates:
  tpl_a:
    components:
      - role: INLINE_ROLE
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
""", encoding="utf-8")

    cfg, _ = load_config(str(root))
    assert cfg.templates["tpl_a"].components[0].role == "INLINE_ROLE"


def test_template_files_not_a_list_is_fatal(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("template_files: not_a_list.yaml\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="template_files must be a list"):
        load_config(str(root))


def test_template_files_missing_file_is_fatal(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("""
template_files:
  - does_not_exist.yaml
""", encoding="utf-8")

    with pytest.raises(ValidationError, match="not found"):
        load_config(str(root))
