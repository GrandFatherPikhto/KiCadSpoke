# kicadspoke/config/__init__.py
"""
config/__init__.py — реэкспорт models.py + loader.py. Публичный интерфейс
пакета НЕ ИЗМЕНИЛСЯ этим рефакторингом: любой существующий
`from kicadspoke.config import Config` / `from ...config import ClonePlacement`
и т.п. по всему остальному проекту продолжает работать один в один — до
рефакторинга kicadspoke/config.py был модулем, теперь kicadspoke/config/ —
пакет с тем же самым набором имён на верхнем уровне.
"""
from .models import (
    ThermalViaArrayConfig,
    TemplateVia,
    TemplateComponentSlot,
    TemplateTrack,
    SpokeTemplate,
    ManualSpoke,
    Rule,
    ClonePlacement,
    Config,
    rule_effective_name,
    thermal_via_array_effective_name,
)
from ..runtime_context import RuntimeContext
from .loader import (
    load_config,
    _load_template_via,
    _load_template_track,
    _load_template_component_slot,
    _load_spoke_template,
    _load_manual_spoke,
    _load_clone_placement,
    _check_layer_value,
)

__all__ = [
    "ThermalViaArrayConfig",
    "TemplateVia",
    "TemplateComponentSlot",
    "TemplateTrack",
    "SpokeTemplate",
    "ManualSpoke",
    "Rule",
    "ClonePlacement",
    "Config",
    "RuntimeContext",
    "load_config",
    "rule_effective_name",
    "thermal_via_array_effective_name",
]