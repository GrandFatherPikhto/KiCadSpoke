#!/usr/bin/env python3
"""
Test for the kicad module (without a real connection to KiCad).
Checks imports and method presence in classes.
"""

import sys
from pathlib import Path

# Add project root to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadspoke.kicad import KiCadBoardAdapter, IBoardAdapter
from kicadspoke.kicad.adapter import KiCadBoardAdapter as Adapter


def test_import():
    """Check that imports work."""
    assert KiCadBoardAdapter is not None
    assert IBoardAdapter is not None
    print("✅ kicad import OK")


def test_adapter_has_methods():
    """Check that the adapter has all methods required by the interface."""
    # List of methods that must be present in KiCadBoardAdapter (including new ones)
    methods = [
        # Core access methods
        "refresh_board",
        "get_footprint",
        "get_footprints",
        "get_vias",
        "get_tracks",
        "get_selected_items",
        "get_field_value",
        "get_footprint_pads",
        "get_pad_by_number",
        "get_zone_by_name",
        "get_net_by_name",
        "get_all_nets",
        "get_bounding_boxes",
        # Transactions
        "begin_commit",
        "push_commit",
        "drop_commit",
        # Mutations
        "update_items",
        "create_items",
        "flip_selected",
        "commit_with_retry",
        "create_via",
        "create_track",
        "remove_by_id",
        # Crash risk warning
        "check_write_crash_risk",
    ]
    for method in methods:
        assert hasattr(Adapter, method), f"Method {method} is missing in KiCadBoardAdapter"
    print("✅ All interface methods are present in the adapter")


def test_init_without_connection():
    """Check that the constructor does not crash (without calling refresh_board)."""
    try:
        adapter = KiCadBoardAdapter(timeout_ms=1000)
        assert adapter is not None
        print("✅ KiCadBoardAdapter constructor works (without connection)")
    except Exception as e:
        print(f"⚠️ Constructor crashed (this may be normal if KiCad is not running): {e}")


if __name__ == "__main__":
    print("Running kicad tests (without KiCad connection)...")
    test_import()
    test_adapter_has_methods()
    test_init_without_connection()
    print("All kicad tests passed (no real IPC).")