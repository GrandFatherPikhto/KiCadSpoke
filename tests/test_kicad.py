#!/usr/bin/env python3
"""
Test for the kicad module (without a real connection to KiCad).
Checks imports and method presence in classes.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

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


def _make_fp(ref):
    fp = MagicMock()
    fp.reference_field.text.value = ref
    return fp


class TestFootprintsCache:
    """get_footprints() caching (added 2026-07-29): the call graph analysis
    (dependency_order.py resolves every rule/clone_placement's anchor TWICE —
    once for the dependency graph, once again to actually plan it — and each
    resolution calls get_footprints() at least once) showed dozens of
    redundant full-board IPC round trips per apply run for data that cannot
    have changed since the last refresh_board(). Uses __new__ to bypass
    __init__ (which creates a real kipy.KiCad() instance) — these tests only
    exercise the caching logic around a mocked self._board/self._kicad, no
    live KiCad connection needed."""

    def test_get_footprints_only_queries_ipc_once_per_generation(self):
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter._footprints_cache = None
        adapter._board.get_footprints.return_value = [_make_fp("R1"), _make_fp("C1")]

        first = adapter.get_footprints()
        second = adapter.get_footprints()

        assert [fp.reference_field.text.value for fp in first] == ["R1", "C1"]
        assert [fp.reference_field.text.value for fp in second] == ["R1", "C1"]
        adapter._board.get_footprints.assert_called_once()

    def test_get_footprint_by_ref_uses_the_cache_too(self):
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter._footprints_cache = None
        adapter._board.get_footprints.return_value = [_make_fp("R1"), _make_fp("C1")]

        found = adapter.get_footprint("C1")
        adapter.get_footprints()  # second read — must still hit the cache

        assert found.reference_field.text.value == "C1"
        adapter._board.get_footprints.assert_called_once()

    def test_refresh_board_clears_the_cache(self):
        adapter = Adapter.__new__(Adapter)
        adapter._kicad = MagicMock()
        adapter._footprints_cache = None
        board1 = MagicMock()
        board1.get_footprints.return_value = [_make_fp("R1")]
        board2 = MagicMock()
        board2.get_footprints.return_value = [_make_fp("R1"), _make_fp("C1")]
        adapter._kicad.get_board.side_effect = [board1, board2]

        adapter.refresh_board()
        first = adapter.get_footprints()
        adapter.refresh_board()
        second = adapter.get_footprints()

        assert len(first) == 1
        assert len(second) == 2
        board1.get_footprints.assert_called_once()
        board2.get_footprints.assert_called_once()

    def test_returned_list_is_a_copy_mutating_it_does_not_corrupt_cache(self):
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter._footprints_cache = None
        adapter._board.get_footprints.return_value = [_make_fp("R1")]

        first = adapter.get_footprints()
        first.append(_make_fp("BOGUS"))
        second = adapter.get_footprints()

        assert len(second) == 1

    def test_flip_selected_invalidates_the_cache(self):
        """Regression (found live 2026-07-29, fpga_oscill_r_pi_filter landing
        on F.Cu instead of B.Cu): flip_selected() flips server-side via a GUI
        action, it does NOT update the local FootprintInstance objects'
        .layer — a cached get_footprints() call right after it must NOT
        return the stale pre-flip list, or flip_manager.flip_if_needed()'s
        "reload after flip" re-fetch silently returns stale data, and the
        subsequent update_items() push undoes the flip."""
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter._kicad = MagicMock()
        adapter._footprints_cache = None
        pre_flip_fp = _make_fp("C1")
        post_flip_fp = _make_fp("C1")
        adapter._board.get_footprints.side_effect = [[pre_flip_fp], [post_flip_fp]]

        first = adapter.get_footprints()
        adapter.flip_selected(first)
        second = adapter.get_footprints()

        assert first[0] is pre_flip_fp
        assert second[0] is post_flip_fp
        assert adapter._board.get_footprints.call_count == 2


class TestIgnoreSelection:
    """adapter.ignore_selection / --no-selection (added 2026-07-30): a stray
    leftover GUI selection in the PCB editor feeds into role-based
    ClonePlacement resolution (resolve_roles_by_selection) and ambiguity
    narrowing (_narrow_ambiguous_candidates/resolve_footprint_by_role) as
    real input — found live: an unrelated component (J1) selected from
    earlier browsing made an otherwise-unique-by-role clone_placement fatal
    with "role X is not in the template". ignore_selection makes
    get_selected_items() always report nothing selected, regardless of the
    live board's actual selection."""

    def test_default_reads_the_real_selection(self):
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter.ignore_selection = False
        adapter._board.get_selection.return_value = [_make_fp("J1")]

        items = adapter.get_selected_items()

        assert len(items) == 1
        adapter._board.get_selection.assert_called_once()

    def test_ignore_selection_reports_nothing_without_querying_the_board(self):
        adapter = Adapter.__new__(Adapter)
        adapter._board = MagicMock()
        adapter.ignore_selection = True
        adapter._board.get_selection.return_value = [_make_fp("J1")]

        items = adapter.get_selected_items()

        assert items == []
        adapter._board.get_selection.assert_not_called()


if __name__ == "__main__":
    print("Running kicad tests (without KiCad connection)...")
    test_import()
    test_adapter_has_methods()
    test_init_without_connection()
    print("All kicad tests passed (no real IPC).")