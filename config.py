# decap_placer/config.py

import logging
from dataclasses import dataclass, field
from typing import Optional, Union, Dict, Any, List
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
class PowerViaConfig:
    """
    Силовая переходная виа СПИЦЫ (не путать с GND-стежком компонента!) —
    поднимает цепь питания с обратного слоя (где живут конденсаторы) на
    лицевой (где сам IC1), рядом с конкретным выводом IC1. Цепь берётся
    из net правила (rule.net), отдельной настройки не нужно — виа и так
    физически на этой же спице/цепи.
    """
    enabled: bool = False
    placement: str = "inside"  # inside | outside — та же семантика, что и у компонентов
    offset_mm: float = 0.3
    drill_mm: float = 0.3
    diameter_mm: float = 0.6

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
class SpokeComponent:
    """
    Один компонент на спице (обычно конденсатор, но не обязательно —
    спица не привязана к конкретному типу). pad здесь больше НЕТ — он
    один на всю спицу (см. Spoke.pad), а не дублируется в каждом
    компоненте, как раньше в Assignment.
    """
    ref: str
    placement: str
    offset_mm: float
    via: Optional[Union[bool, Dict[str, Any]]] = None
    # Явное имя силовой цепи компонента — опционально. Если не задано,
    # силовой вывод определяется как "тот, что не GND" (через pad.net.name
    # на живой плате). Явное указание не обязательно, но убирает даже
    # гипотетическую двусмысленность и чуть упрощает код расстановки.
    power_net: Optional[str] = None
    # Переопределение направления силового вывода ТОЛЬКО для этого
    # компонента. None = наследовать со спицы, дальше с глобального
    # Config.power_pin_facing (тот же паттерн приоритета, что и у via:).
    power_pin_facing: Optional[str] = None  # "pad" | "away" | None

@dataclass
class Spoke:
    """
    Спица — все компоненты, физически относящиеся к одному выводу IC1.
    Компонентов может быть 0, 1, 2 или больше (например, второй inside-
    конденсатор, которому не хватило пары 4.7uF, как C27 у VCCINT).
    Спица — чисто ДЕКЛАРАТИВНАЯ группировка ("кто на каком выводе"), она
    НЕ диктует жёсткость геометрии сама по себе — см. planner: раздвижка
    сначала пробует двигать спицу единым блоком (фаза A), и только если
    это невозможно в пределах допуска — расклеивает её на отдельные точки
    (фаза B, обычный relax_1d).
    """
    pad: str
    components: List[SpokeComponent] = field(default_factory=list)
    power_via: Optional[PowerViaConfig] = None
    # Переопределение направления силового вывода для ВСЕХ компонентов
    # этой спицы (если они сами не переопределяют своё). None = наследовать
    # с глобального Config.power_pin_facing.
    power_pin_facing: Optional[str] = None  # "pad" | "away" | None

@dataclass
class Rule:
    net: str
    spokes: List[Spoke]

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
    min_row_spacing_mm: float = 2.0
    # Глобальный дефолт направления силового вывода: "pad" — силовой пин
    # смотрит НА площадку IC1, "away" — от неё. Переопределяется на уровне
    # спицы (Spoke.power_pin_facing) и/или компонента
    # (SpokeComponent.power_pin_facing) — локальное определение имеет
    # приоритет над глобальным, глобальное — над этим дефолтом.
    power_pin_facing: str = "away"
    # Допуск (мм) на "жёсткий" сдвиг спицы целиком при раздвижке (фаза A).
    # Если спице требуется сдвинуться больше этого — она расклеивается на
    # отдельные компоненты (фаза B, relax_1d как раньше).
    max_spoke_rigid_shift_mm: float = 1.5


def resolve_power_pin_facing(component: SpokeComponent, spoke: Spoke, cfg: Config) -> str:
    """
    Разрешает direction "pad"/"away" с приоритетом: компонент -> спица ->
    глобальный конфиг. Тот же принцип, что и у via: (локальное
    определение побеждает, если задано).
    """
    if component.power_pin_facing is not None:
        return component.power_pin_facing
    if spoke.power_pin_facing is not None:
        return spoke.power_pin_facing
    return cfg.power_pin_facing


def _load_via_config(via_data: Dict[str, Any]) -> ViaConfig:
    return ViaConfig(
        enabled=via_data.get('enabled', False),
        net=via_data.get('net', 'GND'),
        drill_mm=via_data.get('drill_mm', 0.3),
        diameter_mm=via_data.get('diameter_mm', 0.6),
        offset_from_cap_mm=via_data.get('offset_from_cap_mm', 1.0),
        direction=via_data.get('direction', 'away_from_pad'),
        count=via_data.get('count', 1),
    )


def _load_power_via_config(pv_data: Dict[str, Any]) -> PowerViaConfig:
    return PowerViaConfig(
        enabled=pv_data.get('enabled', False),
        placement=pv_data.get('placement', 'inside'),
        offset_mm=pv_data.get('offset_mm', 0.3),
        drill_mm=pv_data.get('drill_mm', 0.3),
        diameter_mm=pv_data.get('diameter_mm', 0.6),
    )


def load_config(path: str) -> Config:
    logger.info(f"Загрузка конфигурации из {path}")
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    via = _load_via_config(data.get('via', {}))

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
        spokes = []
        for spoke_data in rule_data.get('spokes', []):
            components = []
            for comp_data in spoke_data.get('components', []):
                components.append(SpokeComponent(
                    ref=comp_data['ref'],
                    placement=comp_data.get('placement', 'outside'),
                    offset_mm=comp_data.get('offset_mm', 1.0),
                    via=comp_data.get('via'),
                    power_net=comp_data.get('power_net'),
                    power_pin_facing=comp_data.get('power_pin_facing'),
                ))

            power_via_data = spoke_data.get('power_via')
            power_via = _load_power_via_config(power_via_data) if power_via_data else None

            spokes.append(Spoke(
                pad=spoke_data['pad'],
                components=components,
                power_via=power_via,
                power_pin_facing=spoke_data.get('power_pin_facing'),
            ))
        rules.append(Rule(net=rule_data['net'], spokes=spokes))

    cfg = Config(
        target_ref=data['target_ref'],
        boundary_zone=data['boundary_zone'],
        side=data.get('side', 'back'),
        rotation_mode=data.get('rotation_mode', 'radial'),
        fixed_angle_deg=data.get('fixed_angle_deg', 0.0),
        via=via,
        thermal_via_array=thermal_via,
        rules=rules,
        min_row_spacing_mm=data.get('min_row_spacing_mm', 2.0),
        power_pin_facing=data.get('power_pin_facing', 'away'),
        max_spoke_rigid_shift_mm=data.get('max_spoke_rigid_shift_mm', 1.5),
    )
    total_components = sum(len(s.components) for r in cfg.rules for s in r.spokes)
    logger.debug(f"Конфигурация загружена: target={cfg.target_ref}, side={cfg.side}, "
                 f"правил={len(cfg.rules)}, спиц={sum(len(r.spokes) for r in cfg.rules)}, "
                 f"компонентов={total_components}")
    return cfg
