# decap_placer/config.py

import logging
from dataclasses import dataclass, field
from typing import Optional, List
import yaml

logger = logging.getLogger(__name__)

@dataclass
class ViaConfig:
    enabled: bool = False
    net: str = "GND"
    drill_mm: float = 0.3
    diameter_mm: float = 0.6
    offset_from_cap_mm: float = 1.0
    direction: str = "away_from_pad"
    count: int = 1

@dataclass
class ThermalViaArrayConfig:
    enabled: bool = False
    target_ref: str = ""
    pad: str = ""
    net: str = "GND"
    rows: int = 4
    cols: int = 4
    margin_mm: float = 0.5
    pattern: str = "grid"
    drill_mm: float = 0.3
    diameter_mm: float = 0.5

@dataclass
class Assignment:
    ref: str
    pad: str
    placement: str
    offset_mm: float
    via: Optional[ViaConfig | bool] = None

@dataclass
class Rule:
    net: str
    assignments: List[Assignment]

@dataclass
class Config:
    target_ref: str
    boundary_zone: str
    side: str
    rotation_mode: str
    fixed_angle_deg: float
    via: ViaConfig
    thermal_via_array: ThermalViaArrayConfig
    rules: List[Rule]

def load_config(path: str) -> Config:
    logger.info(f"Загрузка конфигурации из {path}")
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    via_data = data.get('via', {})
    via = ViaConfig(
        enabled=via_data.get('enabled', False),
        net=via_data.get('net', 'GND'),
        drill_mm=via_data.get('drill_mm', 0.3),
        diameter_mm=via_data.get('diameter_mm', 0.6),
        offset_from_cap_mm=via_data.get('offset_from_cap_mm', 1.0),
        direction=via_data.get('direction', 'away_from_pad'),
        count=via_data.get('count', 1),
    )

    tva_data = data.get('thermal_via_array', {})
    thermal_via = ThermalViaArrayConfig(
        enabled=tva_data.get('enabled', False),
        target_ref=tva_data.get('target_ref', data['target_ref']),
        pad=tva_data.get('pad', ''),
        net=tva_data.get('net', 'GND'),
        rows=tva_data.get('rows', 4),
        cols=tva_data.get('cols', 4),
        margin_mm=tva_data.get('margin_mm', 0.5),
        pattern=tva_data.get('pattern', 'grid'),
        drill_mm=tva_data.get('drill_mm', 0.3),
        diameter_mm=tva_data.get('diameter_mm', 0.5),
    )

    rules = []
    for rule_data in data.get('rules', []):
        assignments = []
        for ass in rule_data.get('assignments', []):
            via_override = ass.get('via')
            if isinstance(via_override, dict):
                via_override = ViaConfig(**via_override)
            assignments.append(Assignment(
                ref=ass['ref'],
                pad=ass['pad'],
                placement=ass.get('placement', 'outside'),
                offset_mm=ass.get('offset_mm', 1.0),
                via=via_override,
            ))
        rules.append(Rule(net=rule_data['net'], assignments=assignments))

    cfg = Config(
        target_ref=data['target_ref'],
        boundary_zone=data['boundary_zone'],
        side=data.get('side', 'back'),
        rotation_mode=data.get('rotation_mode', 'radial'),
        fixed_angle_deg=data.get('fixed_angle_deg', 0.0),
        via=via,
        thermal_via_array=thermal_via,
        rules=rules,
    )
    logger.debug(f"Конфигурация загружена: target={cfg.target_ref}, side={cfg.side}, "
                 f"правил={len(cfg.rules)}")
    return cfg