#!/usr/bin/env python3
"""
Tests for dependency_order.py — the level-by-level (Kahn's algorithm)
ordering that fixes the p5v_led_spoke bug (2026-07-27): an item anchored on a
ref that ANOTHER item in the same apply run is about to move must be planned
AFTER that other item, not against a stale pre-run snapshot.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kipy.geometry import Vector2
from kipy.board_types import Pad, FootprintInstance

from kicadstamp.config import (
    Config, ThermalViaArrayConfig, ClonePlacement, SpokeTemplate, TemplateComponentSlot,
)
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.dependency_order import resolve_execution_order

MM = 1_000_000


def _make_pad(number, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.net.name = net_name
    return pad


def _make_fp(ref, role=None, x_mm=0.0, y_mm=0.0, nets=()):
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp._role = role
    fp._pads = [_make_pad("1", n) for n in nets]
    return fp


def _adapter_for(fps):
    by_ref = {fp.reference_field.text.value: fp for fp in fps}
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_footprint.side_effect = lambda ref: by_ref.get(ref)
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    adapter.get_footprint_pads.side_effect = lambda fp: list(getattr(fp, "_pads", []))
    adapter.get_selected_items.return_value = []
    return adapter


def _clone(name, anchor_ref, template, nets):
    return ClonePlacement(name=name, template=template, origin_x_mm=0.0, origin_y_mm=0.0,
                          anchor_ref=anchor_ref, nets=nets)


def _cfg(clones):
    producer_tpl = SpokeTemplate(
        name="producer_tpl",
        components=[TemplateComponentSlot(role="PRODUCED_ROLE", offset_along_mm=0.0,
                                          offset_across_mm=0.0, angle_deg=0.0)],
    )
    consumer_tpl = SpokeTemplate(
        name="consumer_tpl",
        components=[TemplateComponentSlot(role="OTHER_ROLE", offset_along_mm=0.0,
                                          offset_across_mm=0.0, angle_deg=0.0)],
    )
    return Config(
        layer='F.Cu',
        templates={"producer_tpl": producer_tpl, "consumer_tpl": consumer_tpl},
        thermal_via_array=ThermalViaArrayConfig(enabled=False),
        rules=[],
        clone_placements=clones,
    )


def test_disabled_clone_is_skipped_entirely():
    """A disabled clone_placement anchored on a role that doesn't exist on the
    board at all would fatal if resolved (see resolve_footprint_by_role) — it
    must be skipped BEFORE anchor resolution is even attempted, not just
    excluded from execution later (compute_raw_positions already no-ops for
    it, but _build_items used to still call resolve_clone_anchor_ref on it
    unconditionally)."""
    anchor1 = _make_fp("ANCHOR1")
    p1 = _make_fp("P1", role="PRODUCED_ROLE", nets=["NET_A"])

    clone_enabled = _clone("clone_a", "ANCHOR1", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
    clone_disabled = ClonePlacement(
        name="clone_disabled", template="consumer_tpl", origin_x_mm=0.0, origin_y_mm=0.0,
        anchor_role="NONEXISTENT_ROLE", enabled=False,
    )
    cfg = _cfg([clone_enabled, clone_disabled])

    adapter = _adapter_for([anchor1, p1])
    items = resolve_execution_order(adapter, cfg)

    assert [it.label for it in items] == ["clone_placement 'clone_a'"]


def test_no_dependencies_keeps_original_order():
    """Two clones anchored on stable, pre-existing components — neither
    produces the other's anchor — order must be unchanged (stable sort)."""
    anchor1 = _make_fp("ANCHOR1")
    anchor2 = _make_fp("ANCHOR2")
    p1 = _make_fp("P1", role="PRODUCED_ROLE", nets=["NET_A"])
    c1 = _make_fp("C1", role="OTHER_ROLE", nets=["NET_B"])

    clone_a = _clone("clone_a", "ANCHOR1", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
    clone_b = _clone("clone_b", "ANCHOR2", "consumer_tpl", {"OTHER_ROLE": "NET_B"})
    cfg = _cfg([clone_a, clone_b])

    adapter = _adapter_for([anchor1, anchor2, p1, c1])
    items = resolve_execution_order(adapter, cfg)

    assert [it.label for it in items] == [
        "clone_placement 'clone_a'", "clone_placement 'clone_b'"
    ]


def test_producer_ordered_before_consumer_regardless_of_yaml_order():
    """clone_consumer is anchored on P1 — the ref clone_producer moves.
    Declared FIRST in YAML (wrong order) — resolve_execution_order must still
    put the producer first. This is the exact p5v_led_spoke/p5v_pi_filter shape."""
    anchor1 = _make_fp("ANCHOR1")
    p1 = _make_fp("P1", role="PRODUCED_ROLE", nets=["NET_A"])
    c1 = _make_fp("C1", role="OTHER_ROLE", nets=["NET_B"])

    clone_producer = _clone("producer", "ANCHOR1", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
    clone_consumer = _clone("consumer", "P1", "consumer_tpl", {"OTHER_ROLE": "NET_B"})
    # Declared in the WRONG order: consumer before its producer.
    cfg = _cfg([clone_consumer, clone_producer])

    adapter = _adapter_for([anchor1, p1, c1])
    items = resolve_execution_order(adapter, cfg)

    assert [it.label for it in items] == [
        "clone_placement 'producer'", "clone_placement 'consumer'"
    ]


def test_self_anchored_item_is_not_a_cycle():
    """Found live (p5v_led_spoke): a clone anchored on its OWN role/pad — the
    anchor component is ALSO one of the template's own role slots (extracted
    with itself as origin). That's a benign self-reference, not a real
    cross-item dependency, and must not be flagged as a cycle."""
    anchor_and_role = _make_fp("R1", role="PRODUCED_ROLE", nets=["NET_A"])

    clone_self = _clone("self_anchored", "R1", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
    cfg = _cfg([clone_self])

    adapter = _adapter_for([anchor_and_role])
    items = resolve_execution_order(adapter, cfg)

    assert [it.label for it in items] == ["clone_placement 'self_anchored'"]


def test_cycle_raises_validation_error():
    """clone_a is anchored on what clone_b produces, and clone_b is anchored
    on what clone_a produces — no valid order exists, must fail loudly before
    any board mutation."""
    p_out = _make_fp("P_OUT", role="PRODUCED_ROLE", nets=["NET_A"])
    c_out = _make_fp("C_OUT", role="OTHER_ROLE", nets=["NET_B"])

    clone_a = _clone("clone_a", "C_OUT", "producer_tpl", {"PRODUCED_ROLE": "NET_A"})
    clone_b = _clone("clone_b", "P_OUT", "consumer_tpl", {"OTHER_ROLE": "NET_B"})
    cfg = _cfg([clone_a, clone_b])

    adapter = _adapter_for([p_out, c_out])

    with pytest.raises(ValidationError, match="dependency cycle"):
        resolve_execution_order(adapter, cfg)
