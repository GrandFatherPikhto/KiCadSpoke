#!/usr/bin/env python3
"""Тесты на clone_role_resolver.py — сопоставление роль->ref для TemplatePlacer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kipy.board_types import FootprintInstance

from kicadspoke.config import SpokeTemplate, TemplateComponentSlot, ClonePlacement
from kicadspoke.placement.services.clone_role_resolver import (
    resolve_roles_by_selection, resolve_roles_by_nets
)
from kicadspoke.exceptions import ValidationError


def _make_fp(ref, role, nets=None):
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    fp._role = role
    fp._nets = nets or []
    return fp


def _get_pads(fp):
    pads = []
    for n in fp._nets:
        p = MagicMock()
        p.net.name = n
        pads.append(p)
    return pads


class TestResolveRolesBySelection:
    def _template(self):
        return SpokeTemplate(name="crystal", components=[
            TemplateComponentSlot(role="XTAL"),
            TemplateComponentSlot(role="LOAD_CAP_1"),
            TemplateComponentSlot(role="LOAD_CAP_2"),
        ])

    def test_exact_match_resolves(self):
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [
            _make_fp("Y3", "XTAL"), _make_fp("C20", "LOAD_CAP_1"), _make_fp("C21", "LOAD_CAP_2"),
        ]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        result = resolve_roles_by_selection(adapter, self._template(), "crystal2")
        assert result == {"XTAL": "Y3", "LOAD_CAP_1": "C20", "LOAD_CAP_2": "C21"}

    def test_missing_role_raises(self):
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [_make_fp("Y3", "XTAL"), _make_fp("C20", "LOAD_CAP_1")]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        with pytest.raises(ValidationError, match="LOAD_CAP_2"):
            resolve_roles_by_selection(adapter, self._template(), "crystal2")

    def test_extra_role_not_in_template_raises(self):
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [
            _make_fp("Y3", "XTAL"), _make_fp("C20", "LOAD_CAP_1"),
            _make_fp("C21", "LOAD_CAP_2"), _make_fp("R5", "EXTRA_ROLE"),
        ]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        with pytest.raises(ValidationError, match="EXTRA_ROLE"):
            resolve_roles_by_selection(adapter, self._template(), "crystal2")

    def test_duplicate_role_in_selection_raises(self):
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [
            _make_fp("Y3", "XTAL"), _make_fp("Y4", "XTAL"),
            _make_fp("C20", "LOAD_CAP_1"), _make_fp("C21", "LOAD_CAP_2"),
        ]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        with pytest.raises(ValidationError, match="XTAL"):
            resolve_roles_by_selection(adapter, self._template(), "crystal2")


class TestResolveRolesByNets:
    def _pi_filter_template(self):
        return SpokeTemplate(name="pi_filter", components=[
            TemplateComponentSlot(role="CAP_IN"),
            TemplateComponentSlot(role="CAP_OUT"),
            TemplateComponentSlot(role="FERRITE"),
        ])

    def test_three_identical_filters_not_confused(self):
        """Ключевой сценарий: 3 физически неразличимых П-фильтра — каждый
        должен резолвиться в СВОИ, не чужие компоненты."""
        fps = [
            _make_fp("C10", "CAP_IN", ["GPIO12"]), _make_fp("C11", "CAP_OUT", ["GPIO12_FILTERED"]),
            _make_fp("L1", "FERRITE", ["GPIO12", "GPIO12_FILTERED"]),
            _make_fp("C12", "CAP_IN", ["GPIO13"]), _make_fp("C13", "CAP_OUT", ["GPIO13_FILTERED"]),
            _make_fp("L2", "FERRITE", ["GPIO13", "GPIO13_FILTERED"]),
            _make_fp("C14", "CAP_IN", ["GPIO14"]), _make_fp("C15", "CAP_OUT", ["GPIO14_FILTERED"]),
            _make_fp("L3", "FERRITE", ["GPIO14", "GPIO14_FILTERED"]),
        ]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        tpl = self._pi_filter_template()
        results = {}
        for gpio_num, gpio_name in [(12, "filter_gpio12"), (13, "filter_gpio13"), (14, "filter_gpio14")]:
            clone = ClonePlacement(
                name=gpio_name, template="pi_filter", origin_x_mm=0, origin_y_mm=0,
                nets={"CAP_IN": f"GPIO{gpio_num}", "CAP_OUT": f"GPIO{gpio_num}_FILTERED",
                     "FERRITE": f"GPIO{gpio_num}"},
            )
            results[gpio_name] = resolve_roles_by_nets(adapter, tpl, clone)

        assert results["filter_gpio12"] == {"CAP_IN": "C10", "CAP_OUT": "C11", "FERRITE": "L1"}
        assert results["filter_gpio13"] == {"CAP_IN": "C12", "CAP_OUT": "C13", "FERRITE": "L2"}
        assert results["filter_gpio14"] == {"CAP_IN": "C14", "CAP_OUT": "C15", "FERRITE": "L3"}
        # Ни один ref не должен повториться между разными экземплярами
        all_refs = [ref for r in results.values() for ref in r.values()]
        assert len(all_refs) == len(set(all_refs))

    def test_net_template_with_params_resolves(self):
        tpl = SpokeTemplate(name="dac", components=[
            TemplateComponentSlot(role="DAC_DB1_CAP", net_template="DAC{channel}_DB1"),
        ])
        fps = [_make_fp("C50", "DAC_DB1_CAP", ["DAC2_DB1"]), _make_fp("C51", "DAC_DB1_CAP", ["DAC3_DB1"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(name="dac_ch2", template="dac", origin_x_mm=0, origin_y_mm=0, params={"channel": 2})
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"DAC_DB1_CAP": "C50"}

    def test_explicit_nets_take_priority_over_net_template(self):
        """ClonePlacement.nets должен побеждать net_template шаблона, если задано и то, и другое."""
        tpl = SpokeTemplate(name="t", components=[
            TemplateComponentSlot(role="X", net_template="SHOULD_NOT_BE_USED"),
        ])
        fps = [_make_fp("A", "X", ["REAL_NET"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(name="c", template="t", origin_x_mm=0, origin_y_mm=0, nets={"X": "REAL_NET"})
        result = resolve_roles_by_nets(adapter, tpl, clone)
        assert result == {"X": "A"}

    def test_role_without_any_net_source_raises(self):
        tpl = SpokeTemplate(name="t2", components=[TemplateComponentSlot(role="NO_NET_ROLE")])
        clone = ClonePlacement(name="x", template="t2", origin_x_mm=0, origin_y_mm=0)
        adapter = MagicMock()
        adapter.get_footprints.return_value = []
        with pytest.raises(ValidationError, match="NO_NET_ROLE"):
            resolve_roles_by_nets(adapter, tpl, clone)

    def test_ambiguous_match_raises_with_both_refs(self):
        tpl = SpokeTemplate(name="t3", components=[TemplateComponentSlot(role="X")])
        fps = [_make_fp("A", "X", ["NET1"]), _make_fp("B", "X", ["NET1"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(name="y", template="t3", origin_x_mm=0, origin_y_mm=0, nets={"X": "NET1"})
        with pytest.raises(ValidationError, match="A.*B|B.*A"):
            resolve_roles_by_nets(adapter, tpl, clone)

    def test_no_match_found_raises(self):
        tpl = SpokeTemplate(name="t4", components=[TemplateComponentSlot(role="X")])
        fps = [_make_fp("A", "X", ["SOME_OTHER_NET"])]
        adapter = MagicMock()
        adapter.get_footprints.return_value = fps
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        adapter.get_footprint_pads.side_effect = _get_pads

        clone = ClonePlacement(name="z", template="t4", origin_x_mm=0, origin_y_mm=0, nets={"X": "NO_SUCH_NET"})
        with pytest.raises(ValidationError, match="NO_SUCH_NET"):
            resolve_roles_by_nets(adapter, tpl, clone)
