#!/usr/bin/env python3
"""Тесты на net_resolution.py — трёхслойное разрешение имени цепи TemplatePlacer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadspoke.net_resolution import resolve_net
from kicadspoke.exceptions import ValidationError


class TestResolveNet:
    def test_literal_unchanged(self):
        assert resolve_net("GND", {}, {}) == "GND"

    def test_placeholder_substituted_from_params(self):
        assert resolve_net("DAC{channel}_DB1", {"channel": 2}, {}) == "DAC2_DB1"

    def test_multiple_placeholders(self):
        assert resolve_net("DAC{channel}_CLK_{polarity}", {"channel": 3, "polarity": "N"}, {}) == "DAC3_CLK_N"

    def test_net_overrides_applied_to_literal(self):
        result = resolve_net("/STM32F4xx/BOOT0", {}, {"/STM32F4xx/BOOT0": "/STM32F4xx_2/BOOT0"})
        assert result == "/STM32F4xx_2/BOOT0"

    def test_net_overrides_applied_to_resolved_not_template(self):
        """override должен матчиться на УЖЕ подставленное имя, не на шаблон с плейсхолдером."""
        result = resolve_net("DAC{channel}_DB1", {"channel": 2}, {"DAC2_DB1": "DAC2_DB1_SPECIAL"})
        assert result == "DAC2_DB1_SPECIAL"

    def test_override_for_different_channel_does_not_apply(self):
        """override на DAC2_DB1 не должен затронуть DAC3_DB1."""
        result = resolve_net("DAC{channel}_DB1", {"channel": 3}, {"DAC2_DB1": "SHOULD_NOT_APPLY"})
        assert result == "DAC3_DB1"

    def test_missing_param_raises_fatal_error(self):
        """
        Регрессия на реальный найденный баг: `if params:` пропускал
        .format() целиком при ПУСТОМ params (пустой dict falsy!), из-за
        чего плейсхолдер тихо оставался нетронутым вместо честной ошибки.
        """
        with pytest.raises(ValidationError, match="channel"):
            resolve_net("DAC{channel}_DB1", {}, {})

    def test_missing_one_of_several_params_raises(self):
        with pytest.raises(ValidationError, match="polarity"):
            resolve_net("DAC{channel}_CLK_{polarity}", {"channel": 1}, {})

    def test_extra_unused_params_are_harmless(self):
        """Лишние params, не встречающиеся в шаблоне -- не должны мешать."""
        assert resolve_net("GND", {"channel": 5, "unused": "x"}, {}) == "GND"
