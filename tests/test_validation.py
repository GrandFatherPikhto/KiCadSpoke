#!/usr/bin/env python3
"""Тесты на фатальные предварительные проверки (validation.py), KiCadSpoke 4.0."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock

from kicadspoke.config import (
    Config, ThermalViaArrayConfig, ManualSpoke, SpokeTemplate,
    TemplateComponentSlot, TemplateVia, Rule, ClonePlacement
)
from kicadspoke.exceptions import ValidationError
from kicadspoke.validation import (
    check_templates_and_pads_exist,
    check_role_pool_sufficiency,
    check_no_duplicate_clone_anchors,
    check_clone_nets_exist_on_board,
    check_single_selection_based_clone,
)


def _cfg(rules=None, templates=None, clone_placements=None, layer='B.Cu'):
    return Config(
        layer=layer,
        templates=templates or {"t": SpokeTemplate(name="t", components=[
            TemplateComponentSlot(role="HEAVY"), TemplateComponentSlot(role="LIGHT")
        ])},
        thermal_via_array=ThermalViaArrayConfig(enabled=False),
        rules=rules or [],
        clone_placements=clone_placements or [],
    )


def _make_pad(number):
    pad = MagicMock()
    pad.number = number
    return pad


def _adapter_with_pads(pad_numbers):
    ic1 = MagicMock()
    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: ic1 if ref == "IC1" else None
    pads = {n: _make_pad(n) for n in pad_numbers}
    adapter.get_pad_by_number.side_effect = lambda fp, num: pads.get(num)
    return adapter


class TestTemplatesAndPadsExist:
    def test_valid_config_passes(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", template="t")])])
        adapter = _adapter_with_pads(["17"])
        check_templates_and_pads_exist(adapter, cfg)

    def test_unknown_template_raises(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", template="does_not_exist")])])
        adapter = _adapter_with_pads(["17"])
        with pytest.raises(ValidationError, match="does_not_exist"):
            check_templates_and_pads_exist(adapter, cfg)

    def test_unknown_pad_raises(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="999", template="t")])])
        adapter = _adapter_with_pads(["17"])
        with pytest.raises(ValidationError, match="999"):
            check_templates_and_pads_exist(adapter, cfg)

    def test_target_ref_not_found_raises(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", template="t")])])
        adapter = MagicMock()
        adapter.get_footprint.return_value = None
        with pytest.raises(ValidationError, match="IC1"):
            check_templates_and_pads_exist(adapter, cfg)

    def test_disabled_spoke_not_checked(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[
            ManualSpoke(pad="999", template="does_not_exist", enabled=False)
        ])])
        adapter = _adapter_with_pads(["17"])
        check_templates_and_pads_exist(adapter, cfg)


class TestRolePoolSufficiency:
    def _adapter_with_pool(self, components):
        fps = []
        for ref, role, net_name in components:
            fp = MagicMock()
            fp.reference_field.text.value = ref
            pad = MagicMock()
            pad.net.name = net_name
            fp._pads = [pad]
            fp._role = role
            fps.append(fp)
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = lambda fp: fp._pads
        return adapter

    def test_sufficient_pool_passes(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[
            ManualSpoke(pad="17", template="t"),
            ManualSpoke(pad="26", template="t"),
        ])])
        adapter = self._adapter_with_pool([
            ("C5", "LIGHT", "+3V3"), ("C6", "LIGHT", "+3V3"),
            ("C30", "HEAVY", "+3V3"), ("C31", "HEAVY", "+3V3"),
        ])
        check_role_pool_sufficiency(adapter, cfg)

    def test_insufficient_pool_raises_with_exact_counts(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[
            ManualSpoke(pad="17", template="t"),
            ManualSpoke(pad="26", template="t"),
        ])])
        adapter = self._adapter_with_pool([
            ("C5", "LIGHT", "+3V3"), ("C6", "LIGHT", "+3V3"),
            ("C30", "HEAVY", "+3V3"),
        ])
        with pytest.raises(ValidationError, match="HEAVY"):
            check_role_pool_sufficiency(adapter, cfg)

    def test_wrong_net_component_not_counted(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", template="t")])])
        adapter = self._adapter_with_pool([
            ("C5", "LIGHT", "+3V3"),
            ("C30", "HEAVY", "+1V2_VCCINT"),
        ])
        with pytest.raises(ValidationError, match="HEAVY"):
            check_role_pool_sufficiency(adapter, cfg)

    def test_multiple_rules_checked_independently(self):
        template = SpokeTemplate(name="t", components=[TemplateComponentSlot(role="HEAVY")])
        cfg = _cfg(
            [
                Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", template="t")]),
                Rule(net="+1V2", anchor_ref='IC1', spokes=[ManualSpoke(pad="40", template="t")]),
            ],
            templates={"t": template},
        )
        adapter = self._adapter_with_pool([
            ("C30", "HEAVY", "+3V3"), ("C31", "HEAVY", "+3V3"),
        ])
        with pytest.raises(ValidationError, match="\\+1V2"):
            check_role_pool_sufficiency(adapter, cfg)


class TestNoDuplicateCloneAnchors:
    def test_no_duplicates_passes(self):
        clones = [
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0,
                           anchor_ref="IC1", anchor_pad="17"),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0,
                           anchor_ref="IC1", anchor_pad="18"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        check_no_duplicate_clone_anchors(cfg)

    def test_duplicate_anchor_raises(self):
        clones = [
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0,
                           anchor_ref="IC1", anchor_pad="17"),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0,
                           anchor_ref="IC1", anchor_pad="17"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        with pytest.raises(ValidationError, match="b.*a"):
            check_no_duplicate_clone_anchors(cfg)

    def test_duplicate_role_anchor_raises(self):
        clones = [
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0,
                           anchor_role="MASTER", anchor_sheet="Sheet1"),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0,
                           anchor_role="MASTER", anchor_sheet="Sheet1"),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        with pytest.raises(ValidationError, match="b.*a"):
            check_no_duplicate_clone_anchors(cfg)

    def test_duplicate_name_raises(self):
        clones = [
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0),
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0),
        ]
        cfg = _cfg(rules=[], clone_placements=clones)
        with pytest.raises(ValidationError, match="a"):
            check_no_duplicate_clone_anchors(cfg)


class TestCloneNetsExistOnBoard:
    def _make_net_mock(self, name):
        net = MagicMock()
        net.name = name
        return net

    def test_valid_nets_passes(self):
        tpl = SpokeTemplate(name="t", vias=[TemplateVia(net="GND")])
        clone = ClonePlacement(name="c", template="t", origin_x_mm=0, origin_y_mm=0)
        cfg = _cfg(rules=[], templates={"t": tpl}, clone_placements=[clone])
        adapter = MagicMock()
        adapter.get_all_nets.return_value = [self._make_net_mock("GND")]
        check_clone_nets_exist_on_board(adapter, cfg)  # не должно бросить

    def test_missing_net_raises(self):
        tpl = SpokeTemplate(name="t", vias=[TemplateVia(net="NON_EXISTENT")])
        clone = ClonePlacement(name="c", template="t", origin_x_mm=0, origin_y_mm=0)
        cfg = _cfg(rules=[], templates={"t": tpl}, clone_placements=[clone])
        adapter = MagicMock()
        adapter.get_all_nets.return_value = [self._make_net_mock("GND")]
        with pytest.raises(ValidationError, match="NON_EXISTENT"):
            check_clone_nets_exist_on_board(adapter, cfg)

    def test_via_in_component_slot_checked(self):
        tpl = SpokeTemplate(name="t", components=[
            TemplateComponentSlot(role="X", vias=[TemplateVia(net="VCC")])
        ])
        clone = ClonePlacement(name="c", template="t", origin_x_mm=0, origin_y_mm=0)
        cfg = _cfg(rules=[], templates={"t": tpl}, clone_placements=[clone])
        adapter = MagicMock()
        adapter.get_all_nets.return_value = [self._make_net_mock("GND")]
        with pytest.raises(ValidationError, match="VCC"):
            check_clone_nets_exist_on_board(adapter, cfg)


class TestSingleSelectionBasedClone:
    def test_single_selection_passes(self):
        cfg = _cfg(rules=[], clone_placements=[
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0)
        ])
        check_single_selection_based_clone(cfg)

    def test_two_selection_based_raises(self):
        cfg = _cfg(rules=[], clone_placements=[
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0),
        ])
        with pytest.raises(ValidationError, match="a.*b"):
            check_single_selection_based_clone(cfg)

    def test_mixed_modes_passes(self):
        cfg = _cfg(rules=[], clone_placements=[
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0,
                           nets={"X": "GND"}),
        ])
        check_single_selection_based_clone(cfg)