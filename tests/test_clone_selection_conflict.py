#!/usr/bin/env python3
"""
Тесты на закрытие дыры: несколько ClonePlacement в режиме "по выделению"
в одном прогоне — физически невозможно (в KiCad одно выделение сразу).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadspoke.config import Config, ClonePlacement, ThermalViaArrayConfig, SpokeTemplate
from kicadspoke.validation import check_single_selection_based_clone, check_clone_templates_exist
from kicadspoke.exceptions import ValidationError
from kicadspoke.placement.services.clone_role_resolver import clone_uses_selection_mode


def _cfg(clones, templates=None):
    return Config(
        layer='B.Cu',
        templates=templates or {"t": SpokeTemplate(name="t")},
        thermal_via_array=ThermalViaArrayConfig(enabled=False),
        rules=[], clone_placements=clones,
    )


class TestCloneUsesSelectionMode:
    def test_no_nets_no_params_is_selection_mode(self):
        c = ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0)
        assert clone_uses_selection_mode(c) is True

    def test_nets_present_is_not_selection_mode(self):
        c = ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0, nets={"X": "GND"})
        assert clone_uses_selection_mode(c) is False

    def test_params_present_is_not_selection_mode(self):
        c = ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0, params={"channel": 1})
        assert clone_uses_selection_mode(c) is False

    def test_by_selection_explicit_true_overrides_nets(self):
        """Явный by_selection: true должен считать режим выделения, даже если nets непуст."""
        c = ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0,
                           nets={"X": "GND"}, by_selection=True)
        assert clone_uses_selection_mode(c) is True

    def test_by_selection_false_with_empty_nets_and_params_implicit_selection(self):
        """by_selection: false при пустых nets/params — всё равно режим выделения (старое поведение)."""
        c = ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0, by_selection=False)
        assert clone_uses_selection_mode(c) is True

    def test_anchor_role_does_not_affect_selection_mode(self):
        """Наличие anchor_role не меняет режим, он определяется nets/params/by_selection."""
        c = ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0,
                           anchor_role="SOME_ROLE")
        assert clone_uses_selection_mode(c) is True

        c2 = ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0,
                            anchor_role="SOME_ROLE", nets={"X": "GND"})
        assert clone_uses_selection_mode(c2) is False


class TestCheckSingleSelectionBasedClone:
    def test_single_selection_based_passes(self):
        cfg = _cfg([ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0)])
        check_single_selection_based_clone(cfg)

    def test_two_selection_based_raises_with_both_names(self):
        cfg = _cfg([
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0),
        ])
        with pytest.raises(ValidationError, match="'a'.*'b'|'b'.*'a'"):
            check_single_selection_based_clone(cfg)

    def test_selection_and_nets_based_do_not_conflict(self):
        cfg = _cfg([
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0, nets={"X": "GND"}),
        ])
        check_single_selection_based_clone(cfg)

    def test_disabled_clone_not_counted(self):
        cfg = _cfg([
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0, enabled=False),
        ])
        check_single_selection_based_clone(cfg)

    def test_three_selection_based_still_fatal(self):
        cfg = _cfg([
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0),
            ClonePlacement(name="c", template="t", origin_x_mm=0, origin_y_mm=0),
        ])
        with pytest.raises(ValidationError):
            check_single_selection_based_clone(cfg)

    def test_by_selection_true_with_nets_counts_as_selection_based(self):
        """Даже если есть nets, by_selection: true заставляет считать режим выделения."""
        cfg = _cfg([
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0, nets={"X": "GND"}, by_selection=True),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0, nets={"Y": "GND"}),
        ])
        # a - selection-based (by_selection), b - nets-based => конфликта нет
        check_single_selection_based_clone(cfg)

        cfg2 = _cfg([
            ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0, by_selection=True),
            ClonePlacement(name="b", template="t", origin_x_mm=0, origin_y_mm=0, by_selection=True),
        ])
        # два с by_selection => фатал
        with pytest.raises(ValidationError):
            check_single_selection_based_clone(cfg2)


class TestCheckCloneTemplatesExist:
    def test_existing_template_passes(self):
        cfg = _cfg([ClonePlacement(name="a", template="t", origin_x_mm=0, origin_y_mm=0)])
        check_clone_templates_exist(cfg)

    def test_missing_template_raises(self):
        cfg = _cfg([ClonePlacement(name="a", template="does_not_exist", origin_x_mm=0, origin_y_mm=0)])
        with pytest.raises(ValidationError, match="does_not_exist"):
            check_clone_templates_exist(cfg)

    def test_disabled_clone_missing_template_not_checked(self):
        cfg = _cfg([ClonePlacement(name="a", template="does_not_exist", origin_x_mm=0, origin_y_mm=0,
                                   enabled=False)])
        check_clone_templates_exist(cfg)  # не должно бросить -- выключена