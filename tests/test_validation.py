#!/usr/bin/env python3
"""Тесты на фатальные предварительные проверки (validation.py)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock

from decap_placer.config import Config, ThermalViaArrayConfig, ManualSpoke, SpokeTemplate, Rule
from decap_placer.exceptions import ValidationError
from decap_placer.validation import check_duplicate_component_refs, check_component_nets


def _cfg(rules):
    return Config(
        target_ref="IC1", side="back",
        templates={"t": SpokeTemplate(name="t")},
        thermal_via_array=ThermalViaArrayConfig(enabled=False),
        rules=rules,
    )


class TestDuplicateComponentRefs:
    def test_no_duplicates_passes_silently(self):
        cfg = _cfg([
            Rule(net="+3V3", spokes=[
                ManualSpoke(pad="17", template="t", component1_ref="C5", component2_ref="C30"),
                ManualSpoke(pad="26", template="t", component1_ref="C6", component2_ref="C31"),
            ])
        ])
        check_duplicate_component_refs(cfg)  # не должно бросить исключение

    def test_same_ref_in_two_different_spokes_raises(self):
        cfg = _cfg([
            Rule(net="+3V3", spokes=[
                ManualSpoke(pad="17", template="t", component1_ref="C5"),
                ManualSpoke(pad="26", template="t", component1_ref="C5"),  # C5 повторно!
            ])
        ])
        with pytest.raises(ValidationError, match="C5"):
            check_duplicate_component_refs(cfg)

    def test_same_ref_in_same_spoke_both_roles_raises(self):
        """Один и тот же ref по ошибке указан и как component1, и как component2 одной спицы."""
        cfg = _cfg([
            Rule(net="+3V3", spokes=[
                ManualSpoke(pad="17", template="t", component1_ref="C5", component2_ref="C5"),
            ])
        ])
        with pytest.raises(ValidationError, match="C5"):
            check_duplicate_component_refs(cfg)

    def test_duplicate_across_different_rules_raises(self):
        cfg = _cfg([
            Rule(net="+3V3", spokes=[ManualSpoke(pad="17", template="t", component1_ref="C5")]),
            Rule(net="+1V2", spokes=[ManualSpoke(pad="40", template="t", component1_ref="C5")]),
        ])
        with pytest.raises(ValidationError):
            check_duplicate_component_refs(cfg)


class TestComponentNets:
    def _adapter_with_component(self, ref, nets):
        fp = MagicMock()
        pads = []
        for net_name in nets:
            pad = MagicMock()
            pad.net.name = net_name
            pads.append(pad)
        adapter = MagicMock()
        adapter.get_footprint.side_effect = lambda r: fp if r == ref else None
        adapter.get_footprint_pads.return_value = pads
        return adapter

    def test_component_on_correct_net_passes(self):
        cfg = _cfg([Rule(net="+3V3_VCCIO", spokes=[
            ManualSpoke(pad="17", template="t", component1_ref="C5")
        ])])
        adapter = self._adapter_with_component("C5", ["+3V3_VCCIO", "GND"])
        check_component_nets(adapter, cfg)  # не должно бросить

    def test_component_on_wrong_net_raises(self):
        """C5 реально сидит на +1V2_VCCINT, а конфиг требует +3V3_VCCIO -- явная опечатка в ref."""
        cfg = _cfg([Rule(net="+3V3_VCCIO", spokes=[
            ManualSpoke(pad="17", template="t", component1_ref="C5")
        ])])
        adapter = self._adapter_with_component("C5", ["+1V2_VCCINT", "GND"])
        with pytest.raises(ValidationError, match="C5"):
            check_component_nets(adapter, cfg)

    def test_component_not_found_raises(self):
        cfg = _cfg([Rule(net="+3V3_VCCIO", spokes=[
            ManualSpoke(pad="17", template="t", component1_ref="C999")
        ])])
        adapter = MagicMock()
        adapter.get_footprint.return_value = None
        with pytest.raises(ValidationError, match="C999"):
            check_component_nets(adapter, cfg)
