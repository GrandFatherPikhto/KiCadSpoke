#!/usr/bin/env python3
"""Тесты на фатальные предварительные проверки (validation.py), KiCadSpoke 4.0."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock

from kicadspoke.config import (
    Config, ThermalViaArrayConfig, ManualSpoke, SpokeTemplate,
    TemplateComponentSlot, Rule
)
from kicadspoke.exceptions import ValidationError
from kicadspoke.validation import check_templates_and_pads_exist, check_role_pool_sufficiency


def _cfg(rules, templates=None):
    return Config(
        layer='B.Cu',
        templates=templates or {"t": SpokeTemplate(name="t", components=[
            TemplateComponentSlot(role="HEAVY"), TemplateComponentSlot(role="LIGHT")
        ])},
        thermal_via_array=ThermalViaArrayConfig(enabled=False),
        rules=rules,
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
        check_templates_and_pads_exist(adapter, cfg)  # не должно бросить

    def test_unknown_template_raises(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", template="does_not_exist")])])
        adapter = _adapter_with_pads(["17"])
        with pytest.raises(ValidationError, match="does_not_exist"):
            check_templates_and_pads_exist(adapter, cfg)

    def test_unknown_pad_raises(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="999", template="t")])])
        adapter = _adapter_with_pads(["17"])  # 999 не существует
        with pytest.raises(ValidationError, match="999"):
            check_templates_and_pads_exist(adapter, cfg)

    def test_target_ref_not_found_raises(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", template="t")])])
        adapter = MagicMock()
        adapter.get_footprint.return_value = None
        with pytest.raises(ValidationError, match="IC1"):
            check_templates_and_pads_exist(adapter, cfg)

    def test_disabled_spoke_not_checked(self):
        """Выключенная спица (enabled=False) не должна проверяться вовсе."""
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[
            ManualSpoke(pad="999", template="does_not_exist", enabled=False)
        ])])
        adapter = _adapter_with_pads(["17"])
        check_templates_and_pads_exist(adapter, cfg)  # не должно бросить -- спица выключена


class TestRolePoolSufficiency:
    def _adapter_with_pool(self, components):
        """components: список (ref, role, net_name)."""
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
        # Нужно 2 HEAVY + 2 LIGHT -- ровно столько и есть
        adapter = self._adapter_with_pool([
            ("C5", "LIGHT", "+3V3"), ("C6", "LIGHT", "+3V3"),
            ("C30", "HEAVY", "+3V3"), ("C31", "HEAVY", "+3V3"),
        ])
        check_role_pool_sufficiency(adapter, cfg)  # не должно бросить

    def test_insufficient_pool_raises_with_exact_counts(self):
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[
            ManualSpoke(pad="17", template="t"),
            ManualSpoke(pad="26", template="t"),
        ])])
        # Нужно 2 HEAVY, есть только 1
        adapter = self._adapter_with_pool([
            ("C5", "LIGHT", "+3V3"), ("C6", "LIGHT", "+3V3"),
            ("C30", "HEAVY", "+3V3"),
        ])
        with pytest.raises(ValidationError, match="HEAVY"):
            check_role_pool_sufficiency(adapter, cfg)

    def test_wrong_net_component_not_counted(self):
        """Компонент с нужной ролью, но НЕ на той цепи -- не должен засчитываться."""
        cfg = _cfg([Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", template="t")])])
        adapter = self._adapter_with_pool([
            ("C5", "LIGHT", "+3V3"),
            ("C30", "HEAVY", "+1V2_VCCINT"),  # HEAVY, но не на +3V3!
        ])
        with pytest.raises(ValidationError, match="HEAVY"):
            check_role_pool_sufficiency(adapter, cfg)

    def test_multiple_rules_checked_independently(self):
        """Нехватка на одном правиле не должна маскироваться избытком на другом."""
        template = SpokeTemplate(name="t", components=[TemplateComponentSlot(role="HEAVY")])
        cfg = _cfg(
            [
                Rule(net="+3V3", anchor_ref='IC1', spokes=[ManualSpoke(pad="17", template="t")]),
                Rule(net="+1V2", anchor_ref='IC1', spokes=[ManualSpoke(pad="40", template="t")]),
            ],
            templates={"t": template},
        )
        adapter = self._adapter_with_pool([
            ("C30", "HEAVY", "+3V3"), ("C31", "HEAVY", "+3V3"),  # с запасом на +3V3
            # на +1V2 -- вообще ни одного HEAVY
        ])
        with pytest.raises(ValidationError, match="\\+1V2"):
            check_role_pool_sufficiency(adapter, cfg)
