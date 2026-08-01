#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fieldstool.gui.pending import PendingRegistry


def test_stage_and_entries(tmp_path):
    reg = PendingRegistry(tmp_path / "pending.json")
    reg.stage("R1", "Role", "NEW_A")
    reg.stage("R2", "Cluster", "Cl_B")

    entries = reg.entries()
    assert len(entries) == 2
    assert {(e.ref, e.field, e.new_value) for e in entries} == {
        ("R1", "Role", "NEW_A"), ("R2", "Cluster", "Cl_B")}


def test_restaging_same_ref_field_overwrites(tmp_path):
    reg = PendingRegistry(tmp_path / "pending.json")
    reg.stage("R1", "Role", "FIRST")
    reg.stage("R1", "Role", "SECOND")

    entries = reg.entries()
    assert len(entries) == 1 and entries[0].new_value == "SECOND"


def test_remove(tmp_path):
    reg = PendingRegistry(tmp_path / "pending.json")
    reg.stage("R1", "Role", "A")
    reg.stage("R2", "Role", "B")
    reg.remove("R1", "Role")

    assert [e.ref for e in reg.entries()] == ["R2"]


def test_clear(tmp_path):
    reg = PendingRegistry(tmp_path / "pending.json")
    reg.stage("R1", "Role", "A")
    reg.clear()
    assert reg.entries() == []


def test_persists_across_instances(tmp_path):
    path = tmp_path / "pending.json"
    reg1 = PendingRegistry(path)
    reg1.stage("R1", "Role", "A")

    reg2 = PendingRegistry(path)
    assert [e.ref for e in reg2.entries()] == ["R1"]


def test_missing_file_starts_empty(tmp_path):
    reg = PendingRegistry(tmp_path / "does_not_exist.json")
    assert reg.entries() == []


def test_corrupt_file_starts_empty_not_fatal(tmp_path):
    path = tmp_path / "pending.json"
    path.write_text("{not valid json", encoding="utf-8")
    reg = PendingRegistry(path)
    assert reg.entries() == []


def test_as_fields_cfg_groups_by_ref():
    reg = PendingRegistry.__new__(PendingRegistry)
    reg._entries = {("R1", "Role"): "A", ("R1", "Cluster"): "B", ("R2", "Role"): "C"}

    cfg = reg.as_fields_cfg()
    assert cfg == {"R1": {"Role": "A", "Cluster": "B"}, "R2": {"Role": "C"}}
