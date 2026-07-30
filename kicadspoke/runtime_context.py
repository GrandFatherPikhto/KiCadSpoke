# kicadstamp/runtime_context.py
"""
runtime_context.py — runtime-computed data that is NOT part of the YAML config.

Separated from Config so that Config stays a pure description of the YAML
schema. Currently only holds sheet_names (parsed from *.kicad_sch files during
load_config). May be extended with adapter, log_dir, etc. in the future.
"""
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class RuntimeContext:
    """
    Runtime-computed data that complements the static YAML Config.

    Created by load_config() and threaded through the pipeline alongside Config
    wherever sheet_names (or future runtime fields) are needed.
    """
    sheet_names: Dict[str, str] = field(default_factory=dict)
