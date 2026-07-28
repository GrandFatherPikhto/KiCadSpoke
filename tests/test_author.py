#!/usr/bin/env python3
"""Tests for kicadspoke/author.py — build ClonePlacement/Rule in Python,
dump back to YAML, or feed straight into the apply pipeline."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import patch

import yaml

from kicadspoke.config import ClonePlacement, Config, ManualSpoke, Rule, load_config
from kicadspoke.author import (_prune_defaults, apply_config, dump_clone_placements,
                               dump_rules, dump_template)


class TestPruneDefaults:
    def test_drops_default_valued_fields(self):
        cp = ClonePlacement(name="c", origin_x_mm=1.0, origin_y_mm=2.0)
        d = _prune_defaults(cp)
        assert "rotation_deg" not in d      # default 0.0
        assert "enabled" not in d           # default True
        assert "nets" not in d              # default_factory dict, empty

    def test_keeps_required_fields_regardless_of_value(self):
        cp = ClonePlacement(name="c", origin_x_mm=0.0, origin_y_mm=0.0)
        d = _prune_defaults(cp)
        # origin_x_mm/origin_y_mm have no default at all -> always present,
        # even though 0.0 also happens to be a "natural" default-looking value.
        assert d["name"] == "c"
        assert d["origin_x_mm"] == 0.0
        assert d["origin_y_mm"] == 0.0

    def test_keeps_non_default_fields(self):
        cp = ClonePlacement(name="c", origin_x_mm=1.0, origin_y_mm=2.0,
                            rotation_deg=90.0, nets={"X": "NET_A"})
        d = _prune_defaults(cp)
        assert d["rotation_deg"] == 90.0
        assert d["nets"] == {"X": "NET_A"}

    def test_recurses_into_rule_spokes(self):
        rule = Rule(net="GND", spokes=[ManualSpoke(pad="1", template="t", shift_x_mm=2.0)])
        d = _prune_defaults(rule)
        assert d["spokes"] == [{"pad": "1", "template": "t", "shift_x_mm": 2.0}]


class TestDumpRoundTrip:
    def test_clone_placements_round_trip(self, tmp_path):
        clones = [
            ClonePlacement(name="channel_0_ad9707", role="AD_DAC",
                           anchor_role="FPGA", anchor_sheet="Channel_{channel}",
                           nets={"AD_DAC": "/Channel_{channel}/DAC/DAC_OUT_P"},
                           params={"channel": 0},
                           origin_x_mm=0.0, origin_y_mm=25.0, rotation_deg=270.0),
        ]
        out = tmp_path / "generated.yaml"
        dump_clone_placements(clones, str(out))

        cfg = load_config(str(out))
        assert len(cfg.clone_placements) == 1
        loaded = cfg.clone_placements[0]
        original = clones[0]
        assert loaded.name == original.name
        assert loaded.role == original.role
        assert loaded.anchor_role == original.anchor_role
        assert loaded.anchor_sheet == original.anchor_sheet
        assert loaded.nets == original.nets
        assert loaded.params == original.params
        assert loaded.origin_x_mm == original.origin_x_mm
        assert loaded.origin_y_mm == original.origin_y_mm
        assert loaded.rotation_deg == original.rotation_deg

    def test_rules_round_trip(self, tmp_path):
        rules = [
            Rule(net="+3V3_VCCIO", name="+3V3_VCCIO", anchor_role="FPGA",
                spokes=[ManualSpoke(pad="17", template="cap_pair_standard",
                                    shift_y_mm=-0.5, rotation_deg=90.0, cluster="FPGA_PWR_BANK")]),
        ]
        out = tmp_path / "generated_rules.yaml"
        dump_rules(rules, str(out))

        cfg = load_config(str(out))
        assert len(cfg.rules) == 1
        loaded = cfg.rules[0]
        assert loaded.net == "+3V3_VCCIO"
        assert loaded.anchor_role == "FPGA"
        assert len(loaded.spokes) == 1
        assert loaded.spokes[0].pad == "17"
        assert loaded.spokes[0].shift_y_mm == -0.5
        assert loaded.spokes[0].cluster == "FPGA_PWR_BANK"

    def test_minimal_clone_placement_omits_defaults_in_yaml_text(self, tmp_path):
        """Sanity check on the actual written text, not just the round-trip —
        confirms the YAML stays close to hand-written minimal style."""
        clones = [ClonePlacement(name="c", origin_x_mm=1.0, origin_y_mm=2.0)]
        out = tmp_path / "generated.yaml"
        dump_clone_placements(clones, str(out))
        text = out.read_text(encoding="utf-8")
        assert "rotation_deg" not in text
        assert "enabled" not in text


class TestApplyConfig:
    def test_builds_namespace_with_every_field_cmd_apply_reads(self):
        """Regression guard: if cmd_apply grows a new required args.* read,
        this test must be updated too — otherwise apply_config would silently
        stop forwarding it and fail at runtime with AttributeError."""
        cfg = Config()
        with patch("kicadspoke_cli.cmd_apply") as mock_cmd_apply:
            apply_config(cfg, "my_run.yaml", dry_run=True, only=["a"], cluster=["b"],
                        timeout_ms=1234, batch_size=5, no_collision_check=True,
                        collision_margin=0.5)

        mock_cmd_apply.assert_called_once()
        call_args, call_kwargs = mock_cmd_apply.call_args
        args = call_args[0]
        assert call_kwargs["cfg"] is cfg
        assert args.config == "my_run.yaml"
        assert args.dry_run is True
        assert args.only == ["a"]
        assert args.cluster == ["b"]
        assert args.timeout_ms == 1234
        assert args.batch_size == 5
        assert args.no_collision_check is True
        assert args.collision_margin == 0.5

    def test_defaults_match_cli_defaults(self):
        cfg = Config()
        with patch("kicadspoke_cli.cmd_apply") as mock_cmd_apply:
            apply_config(cfg, "my_run.yaml")

        args = mock_cmd_apply.call_args[0][0]
        assert args.dry_run is False
        assert args.only is None
        assert args.cluster is None
        assert args.no_collision_check is False
        assert args.collision_margin == 0.2


class TestDumpTemplate:
    def test_writes_template_dict_as_is(self, tmp_path):
        template_dict = {"cap_pair_standard": {"components": [
            {"role": "C_IN_BULK", "offset_along_mm": 0.0, "offset_across_mm": 0.0, "angle_deg": 0.0},
        ]}}
        out = tmp_path / "template.yaml"
        dump_template(template_dict, str(out))

        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert loaded == template_dict

    def test_overwrites_rather_than_merges(self, tmp_path):
        """Unlike cmd_extract's merge-into-existing behaviour, dump_template
        always overwrites — a script regenerating its own dedicated file
        should get a clean result, not accumulate stale entries."""
        out = tmp_path / "template.yaml"
        dump_template({"old_name": {"components": []}}, str(out))
        dump_template({"new_name": {"components": []}}, str(out))

        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert loaded == {"new_name": {"components": []}}
