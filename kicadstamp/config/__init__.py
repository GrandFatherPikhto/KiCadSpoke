# kicadstamp/config/__init__.py
"""
config/__init__.py — re-export of models.py + loader.py. The public interface
of the package has NOT CHANGED with this refactoring: any existing
`from kicadstamp.config import Config` / `from ...config import ClonePlacement`
etc. throughout the rest of the project continues to work exactly as before — prior
to the refactoring kicadstamp/config.py was a module, now kicadstamp/config/ is
a package with the same set of names at the top level.
"""
from .models import (
    ThermalViaArrayConfig,
    TemplateVia,
    TemplateComponentSlot,
    TemplateTrack,
    Cell,
    CellPlacement,
    ManualSpoke,
    Rule,
    ClonePlacement,
    Config,
    rule_effective_name,
    thermal_via_array_effective_name,
)
from .points import Point
from ..runtime_context import RuntimeContext
from .loader import (
    load_config,
    _load_template_via,
    _load_template_track,
    _load_template_component_slot,
    _load_cell,
    _load_cell_placement,
    _load_point,
    _load_manual_spoke,
    _load_clone_placement,
    _load_thermal_via_array,
    _check_layer_value,
)

# Public aliases for the loader entry points the GUI uses to validate/rebuild
# a single entry (Phase 4.2 — gui/ must not import the private names).
load_clone_placement = _load_clone_placement
load_thermal_via_array = _load_thermal_via_array

__all__ = [
    "ThermalViaArrayConfig",
    "TemplateVia",
    "TemplateComponentSlot",
    "TemplateTrack",
    "Cell",
    "CellPlacement",
    "Point",
    "ManualSpoke",
    "Rule",
    "ClonePlacement",
    "Config",
    "RuntimeContext",
    "load_config",
    "load_clone_placement",
    "load_thermal_via_array",
    "rule_effective_name",
    "thermal_via_array_effective_name",
]