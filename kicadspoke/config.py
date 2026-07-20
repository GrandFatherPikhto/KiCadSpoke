# kicadspoke/config.py

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import yaml

from .exceptions import ValidationError, format_fatal_error

logger = logging.getLogger(__name__)


@dataclass
class ThermalViaArrayConfig:
    """Конфигурация массива тепловых via под термопадом IC."""
    enabled: bool = False
    anchor_ref: str = ""
    pad: str = ""
    net: str = "GND"
    rows: int = 4
    cols: int = 4
    margin_mm: float = 0.5
    pattern: str = "grid"
    drill_mm: float = 0.3
    diameter_mm: float = 0.5


@dataclass
class TemplateVia:
    """
    Via-слот шаблона — координаты ВСЕГДА along/across от нуля СПИЦЫ (не от
    пада компонента, даже если слот принадлежит конкретной роли компонента!)
    — та же самая формула (local_to_absolute), что и для позиции самого
    компонента. net=None означает "взять цепь правила" (rule.net).

    ИЗМЕНЕНО (KiCadSpoke): раньше power_via был единственным полем на
    уровне спицы, а GND via компонента считалась от РЕАЛЬНОГО пада уже
    размещённого компонента (требовало чтения живой платы после коммита).
    Теперь обе идеи — один и тот же слот, чистая геометрия шаблона, без
    зависимости от живой платы вообще. Списков может быть сколько угодно,
    на обоих уровнях (spoke.vias и component.vias).
    """
    offset_along_mm: float = 0.0
    offset_across_mm: float = 0.0
    net: Optional[str] = None
    drill_mm: float = 0.3
    diameter_mm: float = 0.6


@dataclass
class TemplateComponentSlot:
    """
    Один компонент-слот в шаблоне — роль ('HEAVY'/'LIGHT'/'XTAL'/
    'LOAD_CAP_1' и т.д.), а не конкретный ref. Роли ОБЯЗАНЫ быть уникальны
    внутри одного шаблона (проверяется фатально при загрузке, см.
    _load_spoke_template). Конкретный ref подбирается на этапе расстановки
    из пула компонентов платы: все футпринты, чей РЕАЛЬНЫЙ пад сидит на
    цепи правила (rule.net) и у кого пользовательское поле Role совпадает
    с этой ролью (см. placement/services/component_pool.py).
    Координаты локальные (along/across) — от нуля СПИЦЫ, не от самого
    компонента. via этого слота — та же локальная система, см. TemplateVia.

    net_template — ОПЦИОНАЛЬНО, для TemplatePlacer (сопоставление роли
    по цепям, а не по выделению): ожидаемая цепь этого компонента, тем же
    синтаксисом плейсхолдеров, что и TemplateVia.net (см.
    net_resolution.py). Для ManualSpoke/component_pool.py не используется
    вообще — там роль ищется по (rule.net, Role), без своего поля здесь.
    """
    role: str
    offset_along_mm: float = 0.0
    offset_across_mm: float = 0.0
    angle_deg: float = 0.0
    vias: List[TemplateVia] = field(default_factory=list)
    net_template: Optional[str] = None
    # Слой слота — ФАКТ, абсолютный: 'F.Cu' | 'B.Cu'. None = наследовать
    # layer шаблона. Пишется extract'ом только для компонентов,
    # выбивающихся из слоя шаблона.
    layer: Optional[str] = None


@dataclass
class SpokeTemplate:
    """
    Шаблон спицы — вся геометрия локальная и поворотоинвариантная:
    описывается один раз при rotation_deg=0 (условный эталонный борт),
    дальше конкретная спица поворачивает его целиком на свой угол.
    Любой из элементов может отсутствовать/быть пустым — например, спица
    без единой via, или шаблон всего с одним компонентом.
    """
    name: str
    vias: List[TemplateVia] = field(default_factory=list)
    components: List[TemplateComponentSlot] = field(default_factory=list)
    # Слой шаблона — ФАКТ, абсолютный: 'F.Cu' | 'B.Cu', как снято
    # экстракцией (пишется автоматически). Компоненты без своего layer
    # наследуют его. Никакой автоматики сторон: шаблон кладётся буква в
    # букву; перевернуть целиком — явный mirror у размещения.
    layer: str = 'F.Cu'


@dataclass
class ManualSpoke:
    """
    Конкретная спица на конкретном паде FPGA. shift_x_mm/shift_y_mm и
    rotation_deg — ВСЕГДА в обычных координатах KiCad (не локальных),
    подбираются глазами под конкретный борт. Порядок применения: сначала
    сдвиг (shift_x, shift_y) от центра пада к нулю спицы, затем поворот
    получившегося нуля (и всего содержимого шаблона) на rotation_deg.

    ВАЖНО: никаких ref компонентов здесь больше нет — конкретные
    компоненты подбираются автоматически из пула (см.
    placement/services/component_pool.py) по совпадению реальной цепи
    (rule.net) и пользовательского поля Role на компоненте, в порядке
    следования спиц в этом списке.
    """
    pad: str
    template: str
    shift_x_mm: float = 0.0
    shift_y_mm: float = 0.0
    rotation_deg: float = 0.0
    enabled: bool = True


@dataclass
class Rule:
    """Правило: выводок спиц вокруг ОДНОГО якорного компонента.
    anchor_ref — чей это выводок (пады спиц — его пады); глобального
    target_ref больше нет, у каждого правила свой якорь."""
    net: str
    spokes: List[ManualSpoke]
    anchor_ref: str = ''


@dataclass
class ClonePlacement:
    """
    Применение шаблона на новом месте (TemplatePlacer/Cloner) — в отличие
    от ManualSpoke (якорь = номер пада IC), якорь здесь — просто имя,
    ни с каким конкретным компонентом не связанное (anchor_id в реестре
    = f"name:{name}"). Два режима позиционирования:
      - anchor_ref задан: ноль = центр пада anchor_pad (или центр
        футпринта, если anchor_pad нет), origin_x_mm/origin_y_mm —
        необязательный ПЛОСКИЙ сдвиг от якоря (без поворота, как shift
        у ManualSpoke), rotation_deg крутит только содержимое шаблона.
      - anchor_ref не задан: origin_x_mm/origin_y_mm — АБСОЛЮТНАЯ
        точка на плате (обязательны), как раньше.

    Сопоставление роль->ref — ЛИБО через текущее выделение на плате
    (для редких, штучных секций типа одной MCU), ЛИБО через явные цепи
    (params/nets/net_overrides — для многократно повторяющихся секций
    вроде П-фильтров или каналов ЦАП). Наличие params ИЛИ nets означает
    режим "по цепям"; их отсутствие — режим "по выделению".
    """
    name: str
    template: str
    origin_x_mm: float
    origin_y_mm: float
    rotation_deg: float = 0.0
    nets: Dict[str, str] = field(default_factory=dict)      # role -> net (буквально)
    params: Dict[str, Any] = field(default_factory=dict)     # для {placeholder} в net шаблона
    net_overrides: Dict[str, str] = field(default_factory=dict)  # финальная подмена resolved-имени
    enabled: bool = True
    anchor_ref: Optional[str] = None
    anchor_pad: Optional[str] = None
    # Слой размещения — ФАКТ: None = слой шаблона (кладём буква в букву).
    # mirror — ОПЕРАЦИЯ, всегда ручная: перевернуть конструкцию целиком
    # (геометрия в зеркало, углы 180°−φ, все слои инвертируются).
    # Противоречие двух твоих же слов — фатал при загрузке: mirror без
    # смены слоя или смена слоя без mirror не имеют физического смысла.
    layer: Optional[str] = None
    mirror: bool = False
    # Явный override роль -> ref (высший приоритет, минуя поиск по цепям):
    # последнее средство, когда кандидаты электрически неразличимы
    # (три одинаковых фильтра в одном листе).
    refs: Dict[str, str] = field(default_factory=dict)

@dataclass
class Config:
    """Главный конфигурационный объект."""
    # Слой спиц (ManualSpoke-путь): 'F.Cu' | 'B.Cu'. У clone_placements —
    # свой layer/mirror per-размещение, это поле их не касается.
    layer: str = 'F.Cu'
    templates: Dict[str, SpokeTemplate] = field(default_factory=dict)
    thermal_via_array: ThermalViaArrayConfig = field(default_factory=ThermalViaArrayConfig)
    rules: List[Rule] = field(default_factory=list)
    clone_placements: List[ClonePlacement] = field(default_factory=list)
    place_components: bool = True
    skip_existing_components: bool = False
    # Параметры поиска свободного места -- сейчас используются только для
    # термовиа (у power/GND via ручное позиционирование, поиска нет).
    via_keepout_clearance_mm: float = 0.2
    via_search_step_mm: float = 0.1
    via_search_max_radius_mm: float = 3.0
    via_search_n_directions: int = 8

    @property
    def anchor_refs(self) -> set:
        """Все якорные ref конфига: правила спиц + термовиа."""
        out = {r.anchor_ref for r in self.rules if r.anchor_ref}
        if self.thermal_via_array.enabled and self.thermal_via_array.anchor_ref:
            out.add(self.thermal_via_array.anchor_ref)
        return out


def _load_template_via(data: Dict[str, Any]) -> TemplateVia:
    return TemplateVia(
        offset_along_mm=data.get('offset_along_mm', 0.0),
        offset_across_mm=data.get('offset_across_mm', 0.0),
        net=data.get('net'),
        drill_mm=data.get('drill_mm', 0.3),
        diameter_mm=data.get('diameter_mm', 0.6),
    )


def _check_layer_value(value, where: str):
    if value is not None and value not in ('F.Cu', 'B.Cu'):
        raise ValidationError(format_fatal_error(
            f"недопустимый layer={value!r} {where}",
            ["layer — абсолютный слой, 'F.Cu' или 'B.Cu'"]
        ))


def _load_template_component_slot(data: Dict[str, Any]) -> TemplateComponentSlot:
    if 'side' in data:
        raise ValidationError(format_fatal_error(
            f"устаревшее поле 'side' у слота {data.get('role')!r}",
            ["относительный side упразднён (см. обсуждение v116): слой теперь "
             "ФАКТ и абсолютный — напиши layer: F.Cu или layer: B.Cu, либо "
             "убери поле, чтобы наследовать layer шаблона"]
        ))
    layer = data.get('layer')
    _check_layer_value(layer, f"у слота {data.get('role')!r}")
    return TemplateComponentSlot(
        role=data['role'],
        offset_along_mm=data.get('offset_along_mm', 0.0),
        offset_across_mm=data.get('offset_across_mm', 0.0),
        angle_deg=data.get('angle_deg', 0.0),
        vias=[_load_template_via(v) for v in data.get('vias', [])],
        net_template=data.get('net_template'),
        layer=layer,
    )


def _load_spoke_template(name: str, data: Dict[str, Any]) -> SpokeTemplate:
    components = [_load_template_component_slot(c) for c in data.get('components', [])]

    roles = [c.role for c in components]
    duplicates = {r for r in roles if roles.count(r) > 1}
    if duplicates:
        raise ValidationError(format_fatal_error(
            f"роль повторяется дважды в шаблоне {name!r}",
            [f"роль {r!r} встречается {roles.count(r)} раз в components этого шаблона — "
             f"роли внутри шаблона обязаны быть уникальны (см. anchor_id/template_name/role "
             f"в реестре расстановки)" for r in sorted(duplicates)]
        ))

    if 'reference_side' in data:
        raise ValidationError(format_fatal_error(
            f"устаревшее поле 'reference_side' в шаблоне {name!r}",
            ["переименовано (см. обсуждение v116): напиши layer: F.Cu или "
             "layer: B.Cu — абсолютный слой шаблона, как снято"]
        ))
    layer = data.get('layer', 'F.Cu')
    _check_layer_value(layer, f"в шаблоне {name!r}")

    return SpokeTemplate(
        name=name,
        vias=[_load_template_via(v) for v in data.get('vias', [])],
        components=components,
        layer=layer,
    )


def _load_manual_spoke(data: Dict[str, Any]) -> ManualSpoke:
    return ManualSpoke(
        pad=data['pad'],
        template=data['template'],
        shift_x_mm=data.get('shift_x_mm', 0.0),
        shift_y_mm=data.get('shift_y_mm', 0.0),
        rotation_deg=data.get('rotation_deg', 0.0),
        enabled=data.get('enabled', True),
    )


def _load_clone_placement(data: Dict[str, Any]) -> ClonePlacement:
    name = data['name']
    anchor_ref = data.get('anchor_ref')
    anchor_pad = data.get('anchor_pad')

    if anchor_pad is not None and anchor_ref is None:
        raise ValidationError(format_fatal_error(
            f"anchor_pad без anchor_ref в clone_placement {name!r}",
            [f"anchor_pad={anchor_pad!r} задан, но anchor_ref отсутствует — "
             f"пад сам по себе ничего не значит, укажи чей он (anchor_ref: IC1)"]
        ))

    # В якорном режиме origin_x/y — необязательный сдвиг от якоря (0.0 по
    # умолчанию, как shift у ManualSpoke). Без якоря — обязательная
    # абсолютная точка, как раньше.
    if anchor_ref is None and ('origin_x_mm' not in data or 'origin_y_mm' not in data):
        raise ValidationError(format_fatal_error(
            f"нет ни якоря, ни абсолютных координат в clone_placement {name!r}",
            [f"укажи либо origin_x_mm/origin_y_mm (абсолютная точка на плате), "
             f"либо anchor_ref (+ опционально anchor_pad) для привязки к компоненту"]
        ))

    if 'side' in data:
        raise ValidationError(format_fatal_error(
            f"устаревшее поле 'side' в clone_placement {name!r}",
            ["сторона теперь задаётся явной парой: layer: F.Cu|B.Cu (куда "
             "кладём — факт) + mirror: true (как кладём — операция, только "
             "при смене слоя относительно шаблона)"]
        ))
    layer = data.get('layer')
    _check_layer_value(layer, f"в clone_placement {name!r}")

    return ClonePlacement(
        name=name,
        template=data['template'],
        origin_x_mm=data.get('origin_x_mm', 0.0),
        origin_y_mm=data.get('origin_y_mm', 0.0),
        rotation_deg=data.get('rotation_deg', 0.0),
        nets=data.get('nets', {}) or {},
        params=data.get('params', {}) or {},
        net_overrides=data.get('net_overrides', {}) or {},
        enabled=data.get('enabled', True),
        anchor_ref=anchor_ref,
        anchor_pad=str(anchor_pad) if anchor_pad is not None else None,
        layer=layer,
        mirror=bool(data.get('mirror', False)),
        refs=data.get('refs', {}) or {},
    )


def load_config(path: str) -> Config:
    logger.info(f"Загрузка конфигурации из {path}")
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

    if 'target_ref' in data:
        raise ValidationError(format_fatal_error(
            "устаревшее поле 'target_ref' в корне конфига",
            ["глобальный target_ref упразднён (см. обсуждение v117): у каждого "
             "правила спиц теперь свой якорь — напиши anchor_ref: <ref> внутри "
             "правила в rules; у thermal_via_array — своё поле anchor_ref"]
        ))
    if 'side' in data:
        raise ValidationError(format_fatal_error(
            "устаревшее поле 'side' в корне конфига",
            ["один язык на весь инструмент: напиши layer: F.Cu или layer: B.Cu "
             "(слой спиц ManualSpoke-пути; back -> B.Cu)"]
        ))
    root_layer = data.get('layer', 'F.Cu')
    _check_layer_value(root_layer, "в корне конфига")

    tva_data = data.get('thermal_via_array', {})
    if 'target_ref' in tva_data:
        raise ValidationError(format_fatal_error(
            "устаревшее поле 'target_ref' в thermal_via_array",
            ["переименовано для единообразия: напиши anchor_ref"]
        ))
    thermal_via = ThermalViaArrayConfig(
        enabled=tva_data.get('enabled', False),
        anchor_ref=tva_data.get('anchor_ref', ''),
        pad=tva_data.get('pad', ''),
        net=tva_data.get('net', 'GND'),
        rows=tva_data.get('rows', 4),
        cols=tva_data.get('cols', 4),
        margin_mm=tva_data.get('margin_mm', 0.5),
        pattern=tva_data.get('pattern', 'grid'),
        drill_mm=tva_data.get('drill_mm', 0.3),
        diameter_mm=tva_data.get('diameter_mm', 0.5),
    )

    templates_data = data.get('templates', {})
    templates = {name: _load_spoke_template(name, tdata) for name, tdata in templates_data.items()}

    rules = []
    for rule_data in data.get('rules', []):
        anchor_ref = rule_data.get('anchor_ref', '')
        if not anchor_ref:
            raise ValidationError(format_fatal_error(
                f"правило (цепь {rule_data.get('net')!r}) без anchor_ref",
                ["у правила спиц обязателен anchor_ref: <ref> — компонент, чьи "
                 "пады перечислены в spokes (раньше это был глобальный target_ref)"]
            ))
        spokes = [_load_manual_spoke(spoke_data) for spoke_data in rule_data.get('spokes', [])]
        rules.append(Rule(net=rule_data['net'], spokes=spokes, anchor_ref=anchor_ref))

    clone_placements = [_load_clone_placement(cp) for cp in data.get('clone_placements', [])]

    # Перекрёстная валидация layer/mirror: инструмент ничего не решает за
    # человека, но противоречие двух его же слов — фатал, не молчаливая каша.
    for cp in clone_placements:
        tpl = templates.get(cp.template)
        if tpl is None:
            continue  # отсутствие шаблона обрабатывается на этапе размещения
        placement_layer = cp.layer if cp.layer is not None else tpl.layer
        layer_changed = placement_layer != tpl.layer
        if cp.mirror and not layer_changed:
            raise ValidationError(format_fatal_error(
                f"mirror без смены слоя в clone_placement {cp.name!r}",
                [f"шаблон {cp.template!r} снят с {tpl.layer}, размещение кладётся "
                 f"на {placement_layer} — зеркало без смены стороны физически не "
                 f"существует: либо добавь layer: "
                 f"{'B.Cu' if tpl.layer == 'F.Cu' else 'F.Cu'}, либо убери mirror"]
            ))
        if layer_changed and not cp.mirror:
            raise ValidationError(format_fatal_error(
                f"слой сменён без mirror в clone_placement {cp.name!r}",
                [f"шаблон {cp.template!r} снят с {tpl.layer}, а layer размещения — "
                 f"{placement_layer}: перевёрнутые футпринты на неперевёрнутых "
                 f"местах дадут кашу; добавь mirror: true (перевернуть целиком) "
                 f"или убери layer"]
            ))

    cfg = Config(
        layer=root_layer,
        templates=templates,
        thermal_via_array=thermal_via,
        rules=rules,
        clone_placements=clone_placements,
        place_components=data.get('place_components', True),
        skip_existing_components=data.get('skip_existing_components', False),
        via_keepout_clearance_mm=data.get('via_keepout_clearance_mm', 0.2),
        via_search_step_mm=data.get('via_search_step_mm', 0.1),
        via_search_max_radius_mm=data.get('via_search_max_radius_mm', 3.0),
        via_search_n_directions=data.get('via_search_n_directions', 8),
    )
    total_spokes = sum(len(r.spokes) for r in cfg.rules)
    logger.debug(f"Конфигурация загружена: layer={cfg.layer}, "
                 f"шаблонов={len(cfg.templates)}, правил={len(cfg.rules)}, спиц={total_spokes}, "
                 f"clone_placements={len(cfg.clone_placements)}")
    return cfg
