#!/usr/bin/env python3
"""Unit tests for the extract command's library core (kicadstamp/cli_extract.py)
— the pure logic that the thin CLI wrapper kicadstamp/cli.py.cmd_extract calls.
No live KiCad board needed: the validation paths tested here raise before any
board I/O happens."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.cli_extract import extract_template
from kicadstamp.exceptions import PlacerError


class TestExtractTemplateValidation:
    """Validation that happens before any board I/O — reachable with a dummy
    adapter, proving the core reports bad arguments via PlacerError instead of
    sys.exit/input (see П.2)."""

    def test_origin_pad_without_role_is_fatal(self):
        # The adapter is never touched — the pad-without-role guard raises
        # before extract_template_from_selection is called.
        with pytest.raises(PlacerError, match="--origin-by-component-pad"):
            extract_template(
                adapter=object(),
                name="cell",
                output="out.yaml",
                origin_component_pad="3",
            )

    def test_origin_pad_requires_role_even_with_other_args(self):
        with pytest.raises(PlacerError, match="--origin-by-component-pad"):
            extract_template(
                adapter=object(),
                name="cell",
                output="out.yaml",
                params={"channel": 1},
                net_template_map={"DAC1_DB1": "DAC{channel}_DB1"},
                origin_component_pad="3",
            )
