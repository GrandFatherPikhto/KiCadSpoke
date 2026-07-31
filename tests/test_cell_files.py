#!/usr/bin/env python3
"""Tests for cells_file/cell_files (kicadstamp/config/loader.py) —
external raw-shaped ({name: {...}}, no 'cells:' wrapper) cell files,
independent of include: (see test_config_includes.py for that mechanism).

Renamed from templates_file/template_files 2026-08-01 (the class became
Cell, was SpokeTemplate — these were the one file-list key pair left
behind, see techdocs/handoff/handoff_2026_08_01_metalanguage_p2_p3.md)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import load_config
from kicadstamp.exceptions import ValidationError

ONE_ROLE = """
{name}:
  components:
    - role: {role}
      offset_along_mm: 0.0
      offset_across_mm: 0.0
      angle_deg: 0.0
"""


def test_single_cells_file_still_works(tmp_path):
    (tmp_path / "ext.yaml").write_text(ONE_ROLE.format(name="tpl_a", role="R1"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("cells_file: ext.yaml\n", encoding="utf-8")

    cfg, _ = load_config(str(root))
    assert set(cfg.cells.keys()) == {"tpl_a"}


def test_multiple_cell_files_merge(tmp_path):
    (tmp_path / "a.yaml").write_text(ONE_ROLE.format(name="tpl_a", role="R1"), encoding="utf-8")
    (tmp_path / "b.yaml").write_text(ONE_ROLE.format(name="tpl_b", role="R2"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("""
cell_files:
  - a.yaml
  - b.yaml
""", encoding="utf-8")

    cfg, _ = load_config(str(root))
    assert set(cfg.cells.keys()) == {"tpl_a", "tpl_b"}


def test_cells_file_and_cell_files_combined(tmp_path):
    (tmp_path / "single.yaml").write_text(ONE_ROLE.format(name="tpl_single", role="R1"), encoding="utf-8")
    (tmp_path / "a.yaml").write_text(ONE_ROLE.format(name="tpl_a", role="R2"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("""
cells_file: single.yaml
cell_files:
  - a.yaml
""", encoding="utf-8")

    cfg, _ = load_config(str(root))
    assert set(cfg.cells.keys()) == {"tpl_single", "tpl_a"}


def test_duplicate_name_across_cell_files_is_fatal(tmp_path):
    (tmp_path / "a.yaml").write_text(ONE_ROLE.format(name="dup", role="R1"), encoding="utf-8")
    (tmp_path / "b.yaml").write_text(ONE_ROLE.format(name="dup", role="R2"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("""
cell_files:
  - a.yaml
  - b.yaml
""", encoding="utf-8")

    with pytest.raises(ValidationError, match="duplicate cell 'dup'"):
        load_config(str(root))


def test_duplicate_name_between_cells_file_and_cell_files_is_fatal(tmp_path):
    (tmp_path / "single.yaml").write_text(ONE_ROLE.format(name="dup", role="R1"), encoding="utf-8")
    (tmp_path / "a.yaml").write_text(ONE_ROLE.format(name="dup", role="R2"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("""
cells_file: single.yaml
cell_files:
  - a.yaml
""", encoding="utf-8")

    with pytest.raises(ValidationError, match="duplicate cell 'dup'"):
        load_config(str(root))


def test_inline_cells_override_external(tmp_path):
    (tmp_path / "a.yaml").write_text(ONE_ROLE.format(name="tpl_a", role="EXTERNAL_ROLE"), encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("""
cell_files:
  - a.yaml
cells:
  tpl_a:
    components:
      - role: INLINE_ROLE
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
""", encoding="utf-8")

    cfg, _ = load_config(str(root))
    assert cfg.cells["tpl_a"].components[0].role == "INLINE_ROLE"


def test_cell_files_not_a_list_is_fatal(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("cell_files: not_a_list.yaml\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="cell_files must be a list"):
        load_config(str(root))


def test_cell_files_missing_file_is_fatal(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("""
cell_files:
  - does_not_exist.yaml
""", encoding="utf-8")

    with pytest.raises(ValidationError, match="not found"):
        load_config(str(root))


def test_old_templates_file_key_is_fatal_with_rename_hint(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("templates_file: ext.yaml\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="deprecated.*templates_file"):
        load_config(str(root))


def test_old_template_files_key_is_fatal_with_rename_hint(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text("""
template_files:
  - ext.yaml
""", encoding="utf-8")

    with pytest.raises(ValidationError, match="deprecated.*template_files"):
        load_config(str(root))
