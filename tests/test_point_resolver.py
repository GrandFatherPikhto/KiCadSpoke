#!/usr/bin/env python3
"""Tests for point_resolver.py — resolving a Point to an absolute position
(+ footprint, when eligible). See handoff_2026_07_31_consolidated.md and
config/points.py for the design."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kipy.geometry import Vector2
from kipy.board_types import Pad, FootprintInstance

from kicadstamp.config import Point
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.services.point_resolver import resolve_point, ResolvedPoint

MM = 1_000_000


def _make_pad(number, x_mm, y_mm):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    return pad


def _make_fp(ref, x_mm=0.0, y_mm=0.0, role=None, pads=()):
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp._role = role
    fp._pads = list(pads)
    return fp


def _adapter_for(fps):
    by_ref = {fp.reference_field.text.value: fp for fp in fps}
    adapter = MagicMock()
    adapter.get_footprints.return_value = fps
    adapter.get_footprint.side_effect = lambda ref: by_ref.get(ref)
    adapter.get_field_value.side_effect = lambda fp, name: getattr(fp, "_role", None)
    adapter.get_selected_items.return_value = []
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in getattr(fp, "_pads", []) if p.number == num), None)
    return adapter


class TestResolvePointAnchorRefBase:
    def test_footprint_centre_no_shift(self):
        fp = _make_fp("U5", x_mm=10.0, y_mm=20.0)
        adapter = _adapter_for([fp])
        point = Point(name="p", anchor_ref="U5")

        resolved = resolve_point(adapter, point, resolved_points={})

        assert resolved.position.x == int(10.0 * MM)
        assert resolved.position.y == int(20.0 * MM)
        assert resolved.footprint is fp

    def test_with_shift_moves_position_and_clears_footprint(self):
        fp = _make_fp("U5", x_mm=10.0, y_mm=20.0)
        adapter = _adapter_for([fp])
        point = Point(name="p", anchor_ref="U5", shift_x_mm=2.5, shift_y_mm=-1.0)

        resolved = resolve_point(adapter, point, resolved_points={})

        assert resolved.position.x == int(12.5 * MM)
        assert resolved.position.y == int(19.0 * MM)
        assert resolved.footprint is None

    def test_with_anchor_pad_uses_pad_position_and_no_footprint(self):
        pad = _make_pad("12", x_mm=11.0, y_mm=21.0)
        fp = _make_fp("U5", x_mm=10.0, y_mm=20.0, pads=[pad])
        adapter = _adapter_for([fp])
        point = Point(name="p", anchor_ref="U5", anchor_pad="12")

        resolved = resolve_point(adapter, point, resolved_points={})

        assert resolved.position.x == int(11.0 * MM)
        assert resolved.position.y == int(21.0 * MM)
        assert resolved.footprint is None

    def test_missing_anchor_pad_is_fatal(self):
        fp = _make_fp("U5", pads=[])
        adapter = _adapter_for([fp])
        point = Point(name="p", anchor_ref="U5", anchor_pad="99")

        with pytest.raises(ValidationError, match="has no pad"):
            resolve_point(adapter, point, resolved_points={})

    def test_missing_anchor_ref_is_fatal(self):
        adapter = _adapter_for([])
        point = Point(name="p", anchor_ref="DOES_NOT_EXIST")

        with pytest.raises(ValidationError):
            resolve_point(adapter, point, resolved_points={})


class TestResolvePointAnchorRoleBase:
    def test_role_resolves_to_footprint(self):
        fp = _make_fp("U7", x_mm=5.0, y_mm=6.0, role="FPGA")
        adapter = _adapter_for([fp])
        point = Point(name="p", anchor_role="FPGA")

        resolved = resolve_point(adapter, point, resolved_points={})

        assert resolved.position.x == int(5.0 * MM)
        assert resolved.position.y == int(6.0 * MM)
        assert resolved.footprint is fp


class TestResolvePointXyLiteral:
    def test_literal_xy_no_footprint(self):
        adapter = _adapter_for([])
        point = Point(name="p", xy=(3.0, 4.0))

        resolved = resolve_point(adapter, point, resolved_points={})

        assert resolved.position.x == int(3.0 * MM)
        assert resolved.position.y == int(4.0 * MM)
        assert resolved.footprint is None

    def test_literal_xy_with_shift(self):
        adapter = _adapter_for([])
        point = Point(name="p", xy=(3.0, 4.0))
        # shift_x_mm/shift_y_mm default to 0.0 here — xy+shift together is
        # rejected at load time (see test_points_loading.py), this just
        # confirms the resolver's own math for the (0, 0) default case.
        resolved = resolve_point(adapter, point, resolved_points={})
        assert resolved.position.x == int(3.0 * MM)


class TestResolvePointAnchorPointChain:
    def test_chain_looks_up_already_resolved_point_no_recursion(self):
        """The referenced point must already be in resolved_points — this
        does NOT call the adapter to resolve it again."""
        adapter = MagicMock()  # no methods configured — must not be called
        base = ResolvedPoint(position=Vector2.from_xy(int(10 * MM), int(20 * MM)),
                             footprint=_make_fp("U5"))
        point = Point(name="child", anchor_point="parent", shift_x_mm=1.0)

        resolved = resolve_point(adapter, point, resolved_points={"parent": base})

        assert resolved.position.x == int(11 * MM)
        assert resolved.position.y == int(20 * MM)
        assert resolved.footprint is None  # shift applied — cleared
        adapter.get_footprint.assert_not_called()
        adapter.get_footprints.assert_not_called()

    def test_chain_with_zero_shift_keeps_footprint(self):
        adapter = MagicMock()
        fp = _make_fp("U5")
        base = ResolvedPoint(position=Vector2.from_xy(0, 0), footprint=fp)
        point = Point(name="child", anchor_point="parent")

        resolved = resolve_point(adapter, point, resolved_points={"parent": base})

        assert resolved.footprint is fp

    def test_chain_from_a_base_with_no_footprint_stays_none(self):
        """Chaining off a point that already has no footprint (e.g. it was
        itself shifted) can't magically produce one again, even with zero
        shift here."""
        adapter = MagicMock()
        base = ResolvedPoint(position=Vector2.from_xy(0, 0), footprint=None)
        point = Point(name="child", anchor_point="parent")

        resolved = resolve_point(adapter, point, resolved_points={"parent": base})

        assert resolved.footprint is None
