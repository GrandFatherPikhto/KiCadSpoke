#!/usr/bin/env python3
"""Tests for net_resolution.py — three‑layer net name resolution for TemplatePlacer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadspoke.net_resolution import resolve_net, resolve_placeholder
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
        """Override must match the ALREADY substituted name, not the template with placeholders."""
        result = resolve_net("DAC{channel}_DB1", {"channel": 2}, {"DAC2_DB1": "DAC2_DB1_SPECIAL"})
        assert result == "DAC2_DB1_SPECIAL"

    def test_override_for_different_channel_does_not_apply(self):
        """Override for DAC2_DB1 must not affect DAC3_DB1."""
        result = resolve_net("DAC{channel}_DB1", {"channel": 3}, {"DAC2_DB1": "SHOULD_NOT_APPLY"})
        assert result == "DAC3_DB1"

    def test_missing_param_raises_fatal_error(self):
        """
        Regression for a real bug: `if params:` skipped `.format()` entirely when
        params was EMPTY (empty dict is falsy!), so placeholders silently remained
        unchanged instead of raising a proper error.
        """
        with pytest.raises(ValidationError, match="channel"):
            resolve_net("DAC{channel}_DB1", {}, {})

    def test_missing_one_of_several_params_raises(self):
        with pytest.raises(ValidationError, match="polarity"):
            resolve_net("DAC{channel}_CLK_{polarity}", {"channel": 1}, {})

    def test_extra_unused_params_are_harmless(self):
        """Extra params not used in the template must not interfere."""
        assert resolve_net("GND", {"channel": 5, "unused": "x"}, {}) == "GND"


class TestResolvePlaceholder:
    """resolve_net's substitution engine, extracted so ClonePlacement.anchor_sheet
    can reuse it too (see clone_role_resolver.resolve_anchor_by_role) — same
    behaviour, no net_overrides step."""

    def test_literal_unchanged(self):
        assert resolve_placeholder("Channel_0", {}) == "Channel_0"

    def test_placeholder_substituted(self):
        assert resolve_placeholder("Channel_{channel}", {"channel": 2}) == "Channel_2"

    def test_missing_param_raises_fatal_error(self):
        with pytest.raises(ValidationError, match="channel"):
            resolve_placeholder("Channel_{channel}", {})

    def test_error_message_uses_what_label(self):
        with pytest.raises(ValidationError, match="anchor_sheet"):
            resolve_placeholder("Channel_{channel}", {}, what="anchor_sheet")