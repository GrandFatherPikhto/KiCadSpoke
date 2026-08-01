# tests/gui/test_entry_points.py
"""
Phase 5.3 — the GUI entry points (kicadstamp_gui.py / fieldstool_gui.py)
must stay importable as plain modules after the sys.path cleanup: loading
one defines a callable main() without spawning an event loop, and the
`if __package__` guard (a bare module import has __package__ == "") still
puts the project root on sys.path so the internal `gui`/`kicadstamp`
imports resolve.
"""
import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_entry_point(name: str):
    path = PROJECT_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot build spec for {name}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["kicadstamp_gui", "fieldstool_gui"])
def test_gui_entry_point_imports_and_exposes_main(name):
    module = _load_entry_point(name)
    assert callable(module.main), f"{name}.main should be callable"
