#!/usr/bin/env python3
"""Tests for clone_anchor_id (kicadspoke/placement/services/clone_position_calculator.py)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadspoke.config import ClonePlacement
from kicadspoke.placement.services.clone_position_calculator import clone_anchor_id


def _clone(**kwargs):
    defaults = dict(name="c", template="t", origin_x_mm=0.0, origin_y_mm=0.0)
    defaults.update(kwargs)
    return ClonePlacement(**defaults)


class TestCloneAnchorId:
    def test_anchor_role_includes_offset(self):
        a = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                    origin_x_mm=7.0, origin_y_mm=-6.0))
        b = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                    origin_x_mm=7.0, origin_y_mm=6.0))
        assert a != b

    def test_anchor_ref_includes_offset(self):
        a = clone_anchor_id(_clone(anchor_ref="IC1", anchor_pad="17",
                                    origin_x_mm=1.0, origin_y_mm=1.0))
        b = clone_anchor_id(_clone(anchor_ref="IC1", anchor_pad="17",
                                    origin_x_mm=2.0, origin_y_mm=1.0))
        assert a != b

    def test_same_anchor_same_offset_is_same_id(self):
        a = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                    origin_x_mm=7.0, origin_y_mm=-6.0))
        b = clone_anchor_id(_clone(anchor_role="CONN_PM5V", anchor_pad="1",
                                    origin_x_mm=7.0, origin_y_mm=-6.0))
        assert a == b

    def test_name_mode_unaffected_by_offset(self):
        """No anchor_ref/anchor_role at all -> identity is name-based, as before."""
        a = clone_anchor_id(_clone(name="x", origin_x_mm=1.0, origin_y_mm=2.0))
        b = clone_anchor_id(_clone(name="x", origin_x_mm=99.0, origin_y_mm=-99.0))
        assert a == b == "name:x"
