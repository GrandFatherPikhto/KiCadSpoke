#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import yaml

from fieldstool.exceptions import FieldsToolError
from fieldstool.rename_fields import plan_rename_edits
from tests.fieldstool_fixtures import sch_file, symbol_block


def _write_config(tmp_path, root_sheet, renames):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"root_sheet": root_sheet, "renames": renames}), encoding="utf-8")
    return config


def test_plan_rename_matches_current_value(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role="OLD_A")), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"Role": {"OLD_A": "NEW_A"}})

    edits_by_file, file_texts, report, unmatched = plan_rename_edits(config)

    assert len(report) == 1
    assert report[0].old_value == "OLD_A" and report[0].new_value == "NEW_A"
    assert report[0].kind == "replace"
    assert unmatched == []


def test_plan_rename_never_inserts_a_missing_field(tmp_path):
    """A block with no Role property at all can't match an old_value —
    rename only ever touches an existing value."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"])), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"Role": {"OLD_A": "NEW_A"}})

    edits_by_file, file_texts, report, unmatched = plan_rename_edits(config)
    assert report == []
    assert unmatched == ["Role: 'OLD_A'"]


def test_plan_rename_unmatched_old_value_is_a_warning_not_fatal(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role="SOMETHING_ELSE")), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"Role": {"NEVER_SEEN": "X"}})

    edits_by_file, file_texts, report, unmatched = plan_rename_edits(config)
    assert report == []
    assert unmatched == ["Role: 'NEVER_SEEN'"]


def test_plan_rename_is_idempotent_on_second_run(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role="NEW_A")), encoding="utf-8")  # already renamed
    config = _write_config(tmp_path, "root.kicad_sch", {"Role": {"OLD_A": "NEW_A"}})

    edits_by_file, file_texts, report, unmatched = plan_rename_edits(config)
    assert report == []
    assert unmatched == ["Role: 'OLD_A'"]  # expected — this is the "already applied" case


def test_plan_rename_multi_instance_block_renamed_once_covers_all_refs(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R41", "R50", "R59"], role="OLD_A")), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"Role": {"OLD_A": "NEW_A"}})

    edits_by_file, file_texts, report, unmatched = plan_rename_edits(config)
    assert len(report) == 1
    assert set(report[0].refs) == {"R41", "R50", "R59"}


def test_plan_rename_multi_unit_renames_each_blocks_own_copy(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["U1"], role="OLD_A"), symbol_block(["U1"], role="OLD_A")),
                     encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"Role": {"OLD_A": "NEW_A"}})

    edits_by_file, file_texts, report, unmatched = plan_rename_edits(config)
    assert len(report) == 2


def test_plan_rename_no_conflict_possible_unlike_set(tmp_path):
    """Rename always writes the SAME new value to every match — a shared
    multi-instance block can never produce the ambiguity set_fields.py
    has to detect."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R41", "R50"], role="OLD_A")), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {"Role": {"OLD_A": "NEW_A"}})

    edits_by_file, file_texts, report, unmatched = plan_rename_edits(config)  # must not raise
    assert len(report) == 1


def test_plan_rename_missing_root_sheet_key_is_fatal(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"renames": {"Role": {"A": "B"}}}), encoding="utf-8")
    with pytest.raises(FieldsToolError, match="root_sheet"):
        plan_rename_edits(config)


def test_plan_rename_empty_renames_is_fatal(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"])), encoding="utf-8")
    config = _write_config(tmp_path, "root.kicad_sch", {})
    with pytest.raises(FieldsToolError, match="renames"):
        plan_rename_edits(config)
