#!/usr/bin/env python3
"""Тест на уникальность ролей внутри шаблона (config.py, загрузка YAML)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadspoke.config import _load_spoke_template
from kicadspoke.exceptions import ValidationError


def test_unique_roles_load_fine():
    tpl = _load_spoke_template("t", {"components": [{"role": "LIGHT"}, {"role": "HEAVY"}]})
    assert len(tpl.components) == 2
    assert {c.role for c in tpl.components} == {"LIGHT", "HEAVY"}


def test_duplicate_role_raises_validation_error():
    with pytest.raises(ValidationError, match="LIGHT"):
        _load_spoke_template("t", {"components": [{"role": "LIGHT"}, {"role": "LIGHT"}]})


def test_three_duplicates_all_named_in_error():
    """Несколько разных дублей сразу -- все должны попасть в одно сообщение."""
    with pytest.raises(ValidationError) as exc_info:
        _load_spoke_template("t", {"components": [
            {"role": "A"}, {"role": "A"}, {"role": "B"}, {"role": "B"}, {"role": "C"},
        ]})
    msg = str(exc_info.value)
    assert "'A'" in msg and "'B'" in msg and "'C'" not in msg  # C не дублируется -- не должен упоминаться


def test_single_role_no_duplicates():
    tpl = _load_spoke_template("t", {"components": [{"role": "SOLO"}]})
    assert len(tpl.components) == 1
