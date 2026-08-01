#!/usr/bin/env python3
"""Tests for the pure (no KiCad adapter) apply filters: retired/--only/--cluster —
kicadstamp_cli.py:drop_disabled_rules/apply_only_filter/apply_cluster_filter.
Order matters: retired wins UNCONDITIONALLY, before --only/--cluster (see the
Rule docstring in config/models.py) — --only cannot resurrect a retired rule."""
import sys
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import yaml
from kicadstamp.config import Config, Rule, ManualSpoke, ClonePlacement, ThermalViaArrayConfig
from kicadstamp.apply_pipeline import (
    _split_comma_values, _matches_any_cluster,
    drop_disabled_rules, drop_inactive_items, apply_only_filter, apply_cluster_filter,
)
from kicadstamp.cli_extract import load_profile, _EXTRACT_PROFILE_KNOWN_KEYS, _CLONE_EXTRACT_PROFILE_KNOWN_KEYS
from kicadstamp.exceptions import PlacerError, ValidationError

logger = logging.getLogger("test_cli_filters")


def _cfg(rules=None, clone_placements=None, thermal_via_array=None):
    return Config(rules=rules or [], clone_placements=clone_placements or [],
                  thermal_via_array=thermal_via_array or ThermalViaArrayConfig())


class TestSplitCommaValues:
    def test_none_or_empty(self):
        assert _split_comma_values(None) == []
        assert _split_comma_values([]) == []

    def test_repeated_flag(self):
        assert _split_comma_values(["a", "b"]) == ["a", "b"]

    def test_comma_within_one_occurrence(self):
        assert _split_comma_values(["a,b"]) == ["a", "b"]

    def test_mixed_repeat_and_comma(self):
        assert _split_comma_values(["a,b", "c"]) == ["a", "b", "c"]

    def test_strips_whitespace(self):
        assert _split_comma_values([" a , b "]) == ["a", "b"]


class TestMatchesAnyCluster:
    def test_none_candidate_never_matches(self):
        assert _matches_any_cluster(None, ["Channel_0"]) is False

    def test_exact_match(self):
        assert _matches_any_cluster("Channel_0", ["Channel_0"]) is True

    def test_segment_prefix_match(self):
        assert _matches_any_cluster("Channel_0/DAC_OA/OA", ["Channel_0"]) is True

    def test_no_false_prefix_on_partial_segment(self):
        assert _matches_any_cluster("Channel_10", ["Channel_1"]) is False

    def test_matches_any_of_several_wanted(self):
        assert _matches_any_cluster("Channel_2/X", ["Channel_0", "Channel_2"]) is True


class TestDropDisabledRules:
    def test_retired_rule_dropped(self):
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[], anchor_role="FPGA", retired=True),
            Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA", retired=False),
        ])
        drop_disabled_rules(cfg, logger)
        assert [r.net for r in cfg.rules] == ["+3V3_VCCIO"]

    def test_only_cannot_resurrect_retired_rule(self):
        """retired:true wins unconditionally — --only naming the very same
        rule must NOT bring it back (it's not even "not found", it plain
        doesn't exist for this run, same as if deleted from the YAML)."""
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[], anchor_role="FPGA", retired=True),
        ])
        drop_disabled_rules(cfg, logger)
        with pytest.raises(PlacerError):
            apply_only_filter(cfg, ["GND"], logger)
        assert cfg.rules == []


class TestDropInactiveItems:
    """skip: true — the inline counterpart of --only/--cluster (skip this
    run, but do NOT prune from the registry, unlike retired: true). See
    Rule/ClonePlacement/ThermalViaArrayConfig.skip in config/models.py."""

    def test_skipped_rule_dropped_non_skipped_rule_kept(self):
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[ManualSpoke(pad="1", cell="t")],
                 anchor_role="FPGA", skip=True),
            Rule(net="+3V3_VCCIO", spokes=[ManualSpoke(pad="2", cell="t")],
                 anchor_role="FPGA", skip=False),
        ])
        drop_inactive_items(cfg, logger)
        assert [r.net for r in cfg.rules] == ["+3V3_VCCIO"]

    def test_skipped_spoke_narrows_rule_without_dropping_it(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", skip=False),
            ManualSpoke(pad="2", cell="t", skip=True),
        ], anchor_role="FPGA")])
        drop_inactive_items(cfg, logger)
        assert len(cfg.rules) == 1
        assert [s.pad for s in cfg.rules[0].spokes] == ["1"]

    def test_rule_dropped_entirely_if_all_spokes_skipped(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", skip=True),
        ], anchor_role="FPGA")])
        drop_inactive_items(cfg, logger)
        assert cfg.rules == []

    def test_original_rule_object_not_mutated(self):
        original = Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", skip=False),
            ManualSpoke(pad="2", cell="t", skip=True),
        ], anchor_role="FPGA")
        cfg = _cfg(rules=[original])
        drop_inactive_items(cfg, logger)
        assert len(original.spokes) == 2

    def test_skipped_clone_placement_removed(self):
        cfg = _cfg(clone_placements=[
            ClonePlacement(name="a", xy=(0.0, 0.0), cell="t", skip=True),
            ClonePlacement(name="b", xy=(0.0, 0.0), cell="t", skip=False),
        ])
        drop_inactive_items(cfg, logger)
        assert [c.name for c in cfg.clone_placements] == ["b"]

    def test_skipped_thermal_via_array_retired_for_this_run(self):
        cfg = _cfg(thermal_via_array=ThermalViaArrayConfig(
            retired=False, anchor_role="FPGA", pad="145", name="fpga_thermal", skip=True,
        ))
        drop_inactive_items(cfg, logger)
        assert cfg.thermal_via_array.retired is True

    def test_skip_false_everywhere_is_noop(self):
        cfg = _cfg(
            rules=[Rule(net="GND", spokes=[ManualSpoke(pad="1", cell="t")], anchor_role="FPGA")],
            clone_placements=[ClonePlacement(name="a", xy=(0.0, 0.0), cell="t")],
            thermal_via_array=ThermalViaArrayConfig(retired=False, anchor_role="FPGA", pad="145", name="th"),
        )
        drop_inactive_items(cfg, logger)
        assert len(cfg.rules) == 1
        assert len(cfg.clone_placements) == 1
        assert cfg.thermal_via_array.retired is False

    def test_skip_true_does_not_affect_known_anchor_ids_computation_order(self):
        """drop_inactive_items only mutates cfg — it must NOT be confused with
        drop_disabled_rules: a rule with retired=False, skip=True still
        contributes to rule_anchor_ids's input set (cfg.rules) at the point
        known_anchor_ids is computed in cmd_apply, i.e. BEFORE this function
        runs. This test just documents that drop_inactive_items itself makes
        no such distinction — it purely filters on .skip."""
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[ManualSpoke(pad="1", cell="t")],
                 anchor_role="FPGA", retired=False, skip=True),
        ])
        assert cfg.rules[0].retired is False
        drop_inactive_items(cfg, logger)
        assert cfg.rules == []


class TestApplyOnlyFilter:
    def test_no_only_names_is_noop(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[], anchor_role="FPGA")])
        apply_only_filter(cfg, [], logger)
        assert len(cfg.rules) == 1

    def test_matches_by_net_when_name_unset(self):
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[], anchor_role="FPGA"),
            Rule(net="+3V3_VCCIO", spokes=[], anchor_role="FPGA"),
        ])
        apply_only_filter(cfg, ["GND"], logger)
        assert [r.net for r in cfg.rules] == ["GND"]

    def test_matches_by_explicit_name(self):
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[], anchor_role="FPGA", name="fpga_gnd"),
        ])
        apply_only_filter(cfg, ["fpga_gnd"], logger)
        assert len(cfg.rules) == 1

    def test_matches_clone_placement_by_name(self):
        cfg = _cfg(clone_placements=[
            ClonePlacement(name="p5v_pi_filter", xy=(0.0, 0.0), cell="t"),
            ClonePlacement(name="other", xy=(0.0, 0.0), cell="t"),
        ])
        apply_only_filter(cfg, ["p5v_pi_filter"], logger)
        assert [c.name for c in cfg.clone_placements] == ["p5v_pi_filter"]

    def test_unknown_name_exits_fatal(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[], anchor_role="FPGA")])
        with pytest.raises(PlacerError):
            apply_only_filter(cfg, ["typo_name"], logger)


class TestApplyClusterFilter:
    def test_no_cluster_paths_is_noop(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", cluster="Channel_0"),
        ], anchor_role="FPGA")])
        apply_cluster_filter(cfg, [], logger)
        assert len(cfg.rules[0].spokes) == 1

    def test_narrows_spokes_within_rule(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", cluster="Channel_0"),
            ManualSpoke(pad="2", cell="t", cluster="Channel_1"),
        ], anchor_role="FPGA")])
        apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert len(cfg.rules) == 1
        assert [s.pad for s in cfg.rules[0].spokes] == ["1"]

    def test_rule_dropped_entirely_if_no_spoke_matches(self):
        # A matching clone_placement keeps the overall filter from fataling
        # on "matched nothing anywhere" — isolates just the rule-dropping behaviour.
        cfg = _cfg(
            rules=[Rule(net="GND", spokes=[
                ManualSpoke(pad="1", cell="t", cluster="Channel_1"),
            ], anchor_role="FPGA")],
            clone_placements=[ClonePlacement(name="ch0", xy=(0.0, 0.0),
                                             cell="t", anchor_cluster="Channel_0")],
        )
        apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert cfg.rules == []

    def test_original_rule_object_not_mutated(self):
        """dataclasses.replace makes a copy — the caller's original Rule.spokes
        list must stay untouched (relevant if the same cfg is reused/logged)."""
        original = Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", cluster="Channel_0"),
            ManualSpoke(pad="2", cell="t", cluster="Channel_1"),
        ], anchor_role="FPGA")
        cfg = _cfg(rules=[original])
        apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert len(original.spokes) == 2

    def test_clone_placement_narrowed_by_anchor_cluster(self):
        cfg = _cfg(clone_placements=[
            ClonePlacement(name="ch0", xy=(0.0, 0.0), cell="t",
                          anchor_cluster="Channel_0"),
            ClonePlacement(name="ch1", xy=(0.0, 0.0), cell="t",
                          anchor_cluster="Channel_1"),
        ])
        apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert [c.name for c in cfg.clone_placements] == ["ch0"]

    def test_thermal_via_array_narrowed_by_anchor_cluster(self):
        # A matching clone_placement keeps the overall filter from fataling
        # on "matched nothing anywhere" — isolates just the thermal behaviour.
        cfg = _cfg(
            clone_placements=[ClonePlacement(name="ch0", xy=(0.0, 0.0),
                                             cell="t", anchor_cluster="Channel_0")],
            thermal_via_array=ThermalViaArrayConfig(
                retired=False, anchor_role="FPGA", pad="145", name="fpga_thermal",
                anchor_cluster="Channel_1",
            ),
        )
        apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert cfg.thermal_via_array.retired is True

    def test_no_match_anywhere_exits_fatal(self):
        cfg = _cfg(rules=[Rule(net="GND", spokes=[
            ManualSpoke(pad="1", cell="t", cluster="Channel_1"),
        ], anchor_role="FPGA")])
        with pytest.raises(PlacerError):
            apply_cluster_filter(cfg, ["Channel_9"], logger)

    def test_only_and_cluster_compose_as_and(self):
        cfg = _cfg(rules=[
            Rule(net="GND", spokes=[
                ManualSpoke(pad="1", cell="t", cluster="Channel_0"),
                ManualSpoke(pad="2", cell="t", cluster="Channel_1"),
            ], anchor_role="FPGA", name="fpga_gnd"),
            Rule(net="+3V3_VCCIO", spokes=[
                ManualSpoke(pad="3", cell="t", cluster="Channel_0"),
            ], anchor_role="FPGA"),
        ])
        apply_only_filter(cfg, ["fpga_gnd"], logger)
        apply_cluster_filter(cfg, ["Channel_0"], logger)
        assert len(cfg.rules) == 1
        assert [s.pad for s in cfg.rules[0].spokes] == ["1"]


class TestLoadProfileRootDefaults:
    """root_defaults on load_profile — a field set once at the file's root
    (sibling to top_key) fills in for any profile that doesn't set it itself;
    a profile that does set it keeps its own value. Added 2026-07-27 so
    extract_profiles entries stop repeating the same output: in every block."""

    def _write(self, tmp_path, data):
        p = tmp_path / "profiles.yaml"
        p.write_text(yaml.safe_dump(data), encoding="utf-8")
        return str(p)

    def test_root_default_fills_missing_field(self, tmp_path):
        path = self._write(tmp_path, {
            "output": "shared.yaml",
            "extract_profiles": {"a": {"name": "a"}},
        })
        prof = load_profile(path, "extract_profiles", "a", root_defaults=["output"])
        assert prof["output"] == "shared.yaml"

    def test_profile_own_value_wins_over_root_default(self, tmp_path):
        path = self._write(tmp_path, {
            "output": "shared.yaml",
            "extract_profiles": {"a": {"name": "a", "output": "own.yaml"}},
        })
        prof = load_profile(path, "extract_profiles", "a", root_defaults=["output"])
        assert prof["output"] == "own.yaml"

    def test_no_root_defaults_requested_unchanged(self, tmp_path):
        """Old call sites (e.g. clone-extract) that don't pass root_defaults
        see no behaviour change — a root-level output: is simply not merged in."""
        path = self._write(tmp_path, {
            "output": "shared.yaml",
            "extract_profiles": {"a": {"name": "a"}},
        })
        prof = load_profile(path, "extract_profiles", "a")
        assert "output" not in prof

    def test_missing_root_field_just_absent(self, tmp_path):
        path = self._write(tmp_path, {
            "extract_profiles": {"a": {"name": "a"}},
        })
        prof = load_profile(path, "extract_profiles", "a", root_defaults=["output"])
        assert "output" not in prof


class TestLoadProfileIncludes:
    """load_profile() resolves include: (kicadstamp/config/includes.py) the
    same way load_config() does, so a subsystem file's extract_profiles/
    clone_profiles are visible here too — not just rules/clone_placements."""

    def test_extract_profiles_from_include_are_visible(self, tmp_path):
        (tmp_path / "sub.yaml").write_text(
            yaml.safe_dump({"extract_profiles": {"b": {"name": "b"}}}),
            encoding="utf-8")
        path = tmp_path / "profiles.yaml"
        path.write_text(yaml.safe_dump({"include": ["sub.yaml"]}), encoding="utf-8")

        prof = load_profile(str(path), "extract_profiles", "b")
        assert prof["name"] == "b"


class TestLoadProfileKnownKeys:
    """known_keys param on load_profile() — regression coverage for the exact
    bug that motivated it (see check_unknown_keys/_EXTRACT_PROFILE_KNOWN_KEYS
    docstrings): a dash instead of underscore ('origin-by-via-net' instead of
    'origin_by_via_net') was previously silently ignored — dict.get() just
    returns None, origin quietly fell back to the selection bbox instead of
    the intended via, no error at all. The fix (check_unknown_keys wired into
    load_profile) had no direct test until now."""

    def _write(self, tmp_path, data):
        p = tmp_path / "profiles.yaml"
        p.write_text(yaml.safe_dump(data), encoding="utf-8")
        return str(p)

    def test_dash_typo_in_extract_profile_is_fatal(self, tmp_path):
        path = self._write(tmp_path, {
            "extract_profiles": {
                "a": {"name": "a", "output": "out.yaml", "origin-by-via-net": "GND"},
            },
        })
        with pytest.raises(ValidationError, match="origin-by-via-net"):
            load_profile(path, "extract_profiles", "a", known_keys=_EXTRACT_PROFILE_KNOWN_KEYS)

    def test_suggests_close_match_for_extract_profile(self, tmp_path):
        path = self._write(tmp_path, {
            "extract_profiles": {
                "a": {"name": "a", "output": "out.yaml", "origin-by-via-net": "GND"},
            },
        })
        with pytest.raises(ValidationError, match="origin_by_via_net"):
            load_profile(path, "extract_profiles", "a", known_keys=_EXTRACT_PROFILE_KNOWN_KEYS)

    def test_all_known_extract_profile_fields_load_fine(self, tmp_path):
        path = self._write(tmp_path, {
            "extract_profiles": {
                "a": {
                    "name": "a", "output": "out.yaml", "params": {"channel": 1},
                    "net_template": {"DAC1_DB1": "DAC{channel}_DB1"},
                    "net_template_role": {"PI_FILTER_FB": "+5V_DIRTY"},
                    "origin_by_via_net": "GND",
                    "origin_by_component_role": "FPGA",
                    "origin_by_component_pad": "3",
                },
            },
        })
        prof = load_profile(path, "extract_profiles", "a", known_keys=_EXTRACT_PROFILE_KNOWN_KEYS)
        assert prof["origin_by_via_net"] == "GND"

    def test_without_known_keys_typo_is_silently_ignored(self, tmp_path):
        """Documents the OLD (still-reachable if a caller omits known_keys)
        behaviour, for contrast with the fatal above — not a recommendation."""
        path = self._write(tmp_path, {
            "extract_profiles": {
                "a": {"name": "a", "output": "out.yaml", "origin-by-via-net": "GND"},
            },
        })
        prof = load_profile(path, "extract_profiles", "a")
        assert "origin_by_via_net" not in prof
        assert "origin-by-via-net" in prof

    def test_dash_typo_in_clone_profile_is_fatal(self, tmp_path):
        path = self._write(tmp_path, {
            "clone_profiles": {
                "a": {"net": "n.net", "pcb": "b.kicad_pcb", "channel": "Channel_0",
                      "out-put": "out.yaml"},
            },
        })
        with pytest.raises(ValidationError, match="out-put"):
            load_profile(path, "clone_profiles", "a", known_keys=_CLONE_EXTRACT_PROFILE_KNOWN_KEYS)

    def test_all_known_clone_profile_fields_load_fine(self, tmp_path):
        path = self._write(tmp_path, {
            "clone_profiles": {
                "a": {"net": "n.net", "pcb": "b.kicad_pcb", "channel": "Channel_0",
                      "output": "out.yaml"},
            },
        })
        prof = load_profile(path, "clone_profiles", "a", known_keys=_CLONE_EXTRACT_PROFILE_KNOWN_KEYS)
        assert prof["output"] == "out.yaml"
