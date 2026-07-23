# kicadspoke/config.py

import logging
import json
import difflib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import yaml

from .exceptions import ValidationError, format_fatal_error
from .sheet_names import build_sheet_name_map

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
class TemplateTrack:
    """
    Прямой отрезок медной дорожки в шаблоне — та же локальная система
    координат (along/across от нуля СПИЦЫ), что и у TemplateVia. Никакой
    привязки к ролям/падам: как и via, дорожка не имеет пользовательских
    полей, "чья" она — определить нельзя, доверяем геометрии (все
    элементы шаблона двигаются/крутятся/зеркалятся одной и той же
    формулой, см. geometry/clone_geometry.py).

    Ломаная дорожка — это просто НЕСКОЛЬКО TemplateTrack подряд,
    стыкующихся концами (ровно так kipy.board_types.Track хранит их и
    внутри самого KiCad — нет отдельной сущности "полилиния"). Дуги
    (ArcTrack) сознательно не поддержаны — не нужны для П-фильтров,
    отдельное расширение при необходимости.

    Коллизии (не пересекает ли дорожка чужую медь/компонент на новом
    месте) НЕ проверяются этим инструментом вообще — сознательное
    решение (см. обсуждение в чате): полагаемся на DRC самого KiCad
    после расстановки, а не строим свою geometry-проверку отрезок-против-
    отрезка.
    """
    start_along_mm: float = 0.0
    start_across_mm: float = 0.0
    end_along_mm: float = 0.0
    end_across_mm: float = 0.0
    width_mm: float = 0.25
    net: Optional[str] = None
    # Слой — тот же паттерн, что у TemplateComponentSlot.layer: None =
    # наследовать layer шаблона, при mirror инвертируется той же строкой.
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
    tracks: List[TemplateTrack] = field(default_factory=list)
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
    # Альтернатива anchor_ref — якорь по полю Role на плате, а не по
    # refdes (переживает реаннотацию/перенумерацию — refdes для этого не
    # надёжен, см. обсуждение). Взаимоисключающе с anchor_ref (фатал при
    # обоих сразу — см. _check_anchor_fields в load_config). anchor_sheet —
    # ТОЛЬКО сужение неоднозначности при 2+ кандидатах с одним anchor_role
    # (сравнение по префиксу имени ЛОКАЛЬНОЙ иерархической цепи вида
    # '/Channel_0/...' — НЕ через sheet_path/UUID, это эмпирически не
    # сработало, см. пробные скрипты в чате). Без смысла без anchor_role.
    anchor_role: Optional[str] = None
    anchor_sheet: Optional[str] = None
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
    # Явный запрос режима "по выделению" — НЕ выводится из отсутствия
    # nets/params (это старое, implicit-поведение остаётся дефолтом для
    # обратной совместимости, см. clone_uses_selection_mode). Нужен
    # отдельно от implicit-режима потому, что params используется ТАКЖЕ
    # для резолва плейсхолдеров via/track (apply_clone_geometry вызывает
    # resolve_net независимо от режима ролей) — без этого флага заданный
    # params для одной лишь via молча и незаметно переключал бы весь
    # clone_placement в режим "по цепям", ломая роли, резолвящиеся по
    # выделению. by_selection: true + непустой nets — фатал при загрузке
    # (противоречие: nets вообще не имеет смысла в режиме "по выделению").
    by_selection: bool = False

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
    # Для anchor_sheet (см. ClonePlacement) — словарь {uuid: Sheetname}
    # строится прямым парсингом *.kicad_sch (sexpdata, тот же формат, что
    # уже читает cloner), НЕ через kipy — см. обсуждение: sheet_path.
    # path_human_readable сломан в этой версии KiCad, а UUID из kipy
    # (path[:-1]) эмпирически подтверждены совпадающими с uuid в
    # (sheet ...) блоках .kicad_sch. schematic_dir — папка, где лежат
    # все *.kicad_sch проекта (путь относительно самого YAML-конфига,
    # как и templates_file); schematic_files — точечные добавки для
    # листов "на отшибе", не лежащих в schematic_dir.
    schematic_dir: Optional[str] = None
    schematic_files: List[str] = field(default_factory=list)
    # Вычисляется в load_config из schematic_dir/schematic_files — НЕ
    # читается из YAML напрямую. {uuid: Sheetname}, пусто если ни
    # schematic_dir, ни schematic_files не заданы (и anchor_sheet тогда
    # использовать нельзя — см. фатал в validation.py).
    sheet_names: Dict[str, str] = field(default_factory=dict)

    @property
    def anchor_refs(self) -> set:
        """Все якорные ref конфига: правила спиц + термовиа."""
        out = {r.anchor_ref for r in self.rules if r.anchor_ref}
        if self.thermal_via_array.enabled and self.thermal_via_array.anchor_ref:
            out.add(self.thermal_via_array.anchor_ref)
        return out


def _load_template_via(data: Dict[str, Any]) -> TemplateVia:
    net = data.get('net')
    if net is not None and not isinstance(net, str):
        raise ValidationError(format_fatal_error(
            f"via.net должен быть строкой, а не {type(net).__name__}",
            [f"получено: {net!r} (offset_along_mm={data.get('offset_along_mm')}, "
             f"offset_across_mm={data.get('offset_across_mm')})",
             "похоже на сломанный YAML — например, net_overrides случайно "
             "вложен под net: этой via вместо того, чтобы быть отдельным полем "
             "верхнего уровня у clone_placements (net_overrides — сосед "
             "template/params у самого clone_placement, не у via)"]
        ))
    return TemplateVia(
        offset_along_mm=data.get('offset_along_mm', 0.0),
        offset_across_mm=data.get('offset_across_mm', 0.0),
        net=net,
        drill_mm=data.get('drill_mm', 0.3),
        diameter_mm=data.get('diameter_mm', 0.6),
    )


def _load_template_track(data: Dict[str, Any]) -> TemplateTrack:
    net = data.get('net')
    if net is not None and not isinstance(net, str):
        raise ValidationError(format_fatal_error(
            f"track.net должен быть строкой, а не {type(net).__name__}",
            [f"получено: {net!r} (start_along_mm={data.get('start_along_mm')}, "
             f"start_across_mm={data.get('start_across_mm')})",
             "похоже на сломанный YAML — например, плейсхолдер вида {NET} без "
             "кавычек: YAML читает его как flow-mapping, а не строку, бери в "
             "кавычки: net: '{NET}'"]
        ))
    layer = data.get('layer')
    _check_layer_value(layer, "у track")
    return TemplateTrack(
        start_along_mm=data.get('start_along_mm', 0.0),
        start_across_mm=data.get('start_across_mm', 0.0),
        end_along_mm=data.get('end_along_mm', 0.0),
        end_across_mm=data.get('end_across_mm', 0.0),
        width_mm=data.get('width_mm', 0.25),
        net=net,
        layer=layer,
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
        tracks=[_load_template_track(t) for t in data.get('tracks', [])],
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


_CLONE_PLACEMENT_KNOWN_KEYS = {
    'name', 'template', 'origin_x_mm', 'origin_y_mm', 'rotation_deg',
    'nets', 'params', 'net_overrides', 'enabled',
    'anchor_ref', 'anchor_pad', 'anchor_role', 'anchor_sheet',
    'layer', 'mirror', 'refs', 'by_selection',
    'side',  # устаревшее — распознаётся отдельно ниже, только чтобы дать
             # осмысленное сообщение про миграцию, а не "неизвестный ключ"
}


def _load_clone_placement(data: Dict[str, Any]) -> ClonePlacement:
    name = data.get('name', '?')
    unknown = set(data.keys()) - _CLONE_PLACEMENT_KNOWN_KEYS
    if unknown:
        problems = []
        for key in sorted(unknown):
            close = difflib.get_close_matches(key, _CLONE_PLACEMENT_KNOWN_KEYS, n=1)
            if not close:
                # difflib не считает 'pad' похожим на 'anchor_pad' (соотношение
                # длин слишком разное) — а это самый частый случай именно тут
                # (anchor_ref/anchor_pad/anchor_role/anchor_sheet все с общим
                # префиксом anchor_). Добавляем отдельно: подстрока в любую сторону.
                close = [k for k in sorted(_CLONE_PLACEMENT_KNOWN_KEYS)
                        if key in k or k in key]
            hint = f" — не имел(а) ли в виду {close[0]!r}?" if close else ""
            problems.append(f"{key!r}{hint}")
        raise ValidationError(format_fatal_error(
            f"неизвестные поля в clone_placement {name!r}",
            [f"незнакомый ключ молча игнорируется, а не падает — типичный "
             f"источник тихой ошибки (например, anchor_pad не сработает, "
             f"если написать просто pad): {', '.join(problems)}"]
        ))

    anchor_ref = data.get('anchor_ref')
    anchor_pad = data.get('anchor_pad')
    anchor_role = data.get('anchor_role')
    anchor_sheet = data.get('anchor_sheet')

    if anchor_ref is not None and anchor_role is not None:
        raise ValidationError(format_fatal_error(
            f"anchor_ref и anchor_role одновременно в clone_placement {name!r}",
            [f"это два взаимоисключающих способа задать якорь — либо по refdes "
             f"(anchor_ref), либо по полю Role (anchor_role), не оба сразу"]
        ))

    if anchor_sheet is not None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            f"anchor_sheet без anchor_role в clone_placement {name!r}",
            [f"anchor_sheet={anchor_sheet!r} задан, но anchor_role отсутствует — "
             f"anchor_sheet только сужает неоднозначность anchor_role, сам по "
             f"себе якорем не является"]
        ))

    if anchor_pad is not None and anchor_ref is None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            f"anchor_pad без anchor_ref/anchor_role в clone_placement {name!r}",
            [f"anchor_pad={anchor_pad!r} задан, но не указано, чей он — "
             f"anchor_ref: IC1 или anchor_role: SOME_ROLE"]
        ))

    has_anchor = anchor_ref is not None or anchor_role is not None

    # В якорном режиме origin_x/y — необязательный сдвиг от якоря (0.0 по
    # умолчанию, как shift у ManualSpoke). Без якоря — обязательная
    # абсолютная точка, как раньше.
    if not has_anchor and ('origin_x_mm' not in data or 'origin_y_mm' not in data):
        raise ValidationError(format_fatal_error(
            f"нет ни якоря, ни абсолютных координат в clone_placement {name!r}",
            [f"укажи либо origin_x_mm/origin_y_mm (абсолютная точка на плате), "
             f"либо anchor_ref/anchor_role (+ опционально anchor_pad) для "
             f"привязки к компоненту"]
        ))

    if 'side' in data:
        raise ValidationError(format_fatal_error(
            f"устаревшее поле 'side' в clone_placement {name!r}",
            ["сторона теперь задаётся явной парой: layer: F.Cu|B.Cu (куда "
             "кладём — факт) + mirror: true (как кладём — операция, только "
             "при смене слоя относительно шаблона)"]
        ))
    by_selection = bool(data.get('by_selection', False))
    nets = data.get('nets', {}) or {}
    if by_selection and nets:
        raise ValidationError(format_fatal_error(
            f"by_selection: true вместе с непустым nets в clone_placement {name!r}",
            [f"nets — это явная карта роль->цепь для режима «по цепям», в режиме "
             f"«по выделению» роли резолвятся мышкой, а не по цепям — nets тут "
             f"бессмысленен. Убери nets, либо убери by_selection: true"]
        ))

    layer = data.get('layer')
    _check_layer_value(layer, f"в clone_placement {name!r}")

    return ClonePlacement(
        name=name,
        template=data['template'],
        origin_x_mm=data.get('origin_x_mm', 0.0),
        origin_y_mm=data.get('origin_y_mm', 0.0),
        rotation_deg=data.get('rotation_deg', 0.0),
        nets=nets,
        params=data.get('params', {}) or {},
        net_overrides=data.get('net_overrides', {}) or {},
        enabled=data.get('enabled', True),
        anchor_ref=anchor_ref,
        anchor_pad=str(anchor_pad) if anchor_pad is not None else None,
        anchor_role=anchor_role,
        anchor_sheet=anchor_sheet,
        layer=layer,
        mirror=bool(data.get('mirror', False)),
        refs=data.get('refs', {}) or {},
        by_selection=by_selection,
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

    templates_data = dict(data.get('templates', {}) or {})
    templates_file = data.get('templates_file')
    if templates_file:
        templates_path = Path(path).parent / templates_file
        if not templates_path.exists():
            raise ValidationError(format_fatal_error(
                f"templates_file {templates_file!r} не найден",
                [f"ожидался по пути {templates_path} (относительно самого конфига "
                 f"{path!r}) — путь пишется относительно расположения ЭТОГО YAML, "
                 f"не текущей директории запуска"]
            ))
        with open(templates_path, 'r', encoding='utf-8') as f:
            if templates_path.suffix.lower() == '.json':
                external_templates = json.load(f)
            else:
                external_templates = yaml.safe_load(f) or {}
        # Инлайновые templates: (если есть) дополняют/переопределяют внешний
        # файл, а не наоборот — библиотека как базовый слой, локальные правки
        # (если вдруг понадобятся) поверх, явно видно в самом конфиге.
        merged = dict(external_templates)
        merged.update(templates_data)
        templates_data = merged
        logger.info(f"Шаблоны из {templates_file}: {len(external_templates)}, "
                   f"плюс инлайновых в самом конфиге: {len(data.get('templates', {}) or {})}")
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

    schematic_dir = data.get('schematic_dir')
    schematic_files = data.get('schematic_files', []) or []
    sheet_names = build_sheet_name_map(path, schematic_dir, schematic_files)

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
        schematic_dir=schematic_dir,
        schematic_files=schematic_files,
        sheet_names=sheet_names,
    )
    total_spokes = sum(len(r.spokes) for r in cfg.rules)
    logger.debug(f"Конфигурация загружена: layer={cfg.layer}, "
                 f"шаблонов={len(cfg.templates)}, правил={len(cfg.rules)}, спиц={total_spokes}, "
                 f"clone_placements={len(cfg.clone_placements)}")
    return cfg