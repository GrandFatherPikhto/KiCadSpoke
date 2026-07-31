#!/usr/bin/env python3
"""Role uniqueness within a cell (config/loader.py, YAML loading)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import _load_cell
from kicadstamp.exceptions import ValidationError


def test_unique_roles_load_fine():
    cell = _load_cell("t", {"components": [{"role": "LIGHT"}, {"role": "HEAVY"}]})
    assert len(cell.components) == 2
    assert {c.role for c in cell.components} == {"LIGHT", "HEAVY"}


def test_duplicate_role_raises_validation_error():
    with pytest.raises(ValidationError, match="LIGHT"):
        _load_cell("t", {"components": [{"role": "LIGHT"}, {"role": "LIGHT"}]})


def test_three_duplicates_all_named_in_error():
    """Several distinct duplicates at once — all must land in one message."""
    with pytest.raises(ValidationError) as exc_info:
        _load_cell("t", {"components": [
            {"role": "A"}, {"role": "A"}, {"role": "B"}, {"role": "B"}, {"role": "C"},
        ]})
    msg = str(exc_info.value)
    assert "'A'" in msg and "'B'" in msg and "'C'" not in msg  # C is not a duplicate — must not be named


def test_single_role_no_duplicates():
    cell = _load_cell("t", {"components": [{"role": "SOLO"}]})
    assert len(cell.components) == 1
