#!/usr/bin/env python3
"""
Integration check that ClonePlacement.ignore_selection (added 2026-07-30)
actually reaches resolve_roles_by_selection via
ClonePositionCalculator.compute_raw_positions ->
adapter.temporarily_ignore_selection — not just that the context manager
itself works in isolation (see TestTemporarilyIgnoreSelection in
test_kicad.py). Reproduces the live bug this was built for: a stray,
unrelated component (J1, Role=CONN_PM5V) selected in the PCB editor made an
otherwise-unique-by-role clone_placement (role: FPGA) fatal, even though
FPGA itself resolves fine without any selection at all.

Uses a real KiCadBoardAdapter (via __new__, bypassing __init__'s live kipy.KiCad
connection) so get_selected_items()/get_footprints() run their REAL
ignore_selection-aware logic — only the board-facing leaf calls
(_board.get_selection/get_footprints, get_field_value) are stubbed.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kipy.board_types import FootprintInstance

from kicadspoke.config import Config, ClonePlacement
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.placement.services.clone_position_calculator import ClonePositionCalculator
from kicadspoke.exceptions import ValidationError


def _make_fp(ref):
    # spec=FootprintInstance so isinstance() checks in resolve_roles_by_selection
    # (footprints = [i for i in items if isinstance(i, FootprintInstance)])
    # actually see this mock as a footprint — a plain MagicMock() fails those.
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    return fp


def _adapter_with_stray_selection():
    """Real adapter, ignore_selection starts False (no --no-selection this
    run). Board has IC1 (Role=FPGA, unique) and J1 (Role=CONN_PM5V,
    currently selected in the GUI but irrelevant to this clone_placement)."""
    adapter = KiCadBoardAdapter.__new__(KiCadBoardAdapter)
    adapter.ignore_selection = False
    adapter._footprints_cache = None
    adapter._board = MagicMock()
    ic1, j1 = _make_fp("IC1"), _make_fp("J1")
    adapter._board.get_footprints.return_value = [ic1, j1]
    adapter._board.get_selection.return_value = [j1]
    roles = {"IC1": "FPGA", "J1": "CONN_PM5V"}
    adapter.get_field_value = MagicMock(side_effect=lambda fp, field: roles[fp.reference_field.text.value])
    return adapter


def _cfg():
    return Config(templates={})


class TestCloneIgnoreSelectionWiring:
    def test_stray_selection_fatals_without_ignore_selection(self):
        adapter = _adapter_with_stray_selection()
        clone = ClonePlacement(name="fpga", role="FPGA", origin_x_mm=0.0, origin_y_mm=0.0)
        calc = ClonePositionCalculator(adapter, _cfg())

        with pytest.raises(ValidationError, match="CONN_PM5V"):
            calc.compute_raw_positions([clone])

    def test_ignore_selection_true_resolves_despite_stray_selection(self):
        adapter = _adapter_with_stray_selection()
        clone = ClonePlacement(name="fpga", role="FPGA", origin_x_mm=0.0, origin_y_mm=0.0,
                               ignore_selection=True)
        calc = ClonePositionCalculator(adapter, _cfg())

        components, vias, tracks = calc.compute_raw_positions([clone])

        assert [c.ref for c in components] == ["IC1"]

    def test_override_is_restored_after_the_clone_is_processed(self):
        """The per-clone override must not leak into whatever runs next in
        the same apply — confirms temporarily_ignore_selection's restore
        actually fires around compute_raw_positions, not just in isolation."""
        adapter = _adapter_with_stray_selection()
        clone = ClonePlacement(name="fpga", role="FPGA", origin_x_mm=0.0, origin_y_mm=0.0,
                               ignore_selection=True)
        calc = ClonePositionCalculator(adapter, _cfg())

        calc.compute_raw_positions([clone])

        assert adapter.ignore_selection is False
