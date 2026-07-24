# kicadspoke/config/loader.py
"""
config/loader.py — вся логика загрузки/валидации YAML в dataclass'ы из
config/models.py: load_config() (точка входа) и все _load_* функции.
Выделено из монолитного config.py тем же рефакторингом, что и models.py —
см. его докстринг.
"""
import logging
import json
import difflib
from pathlib import Path
from typing import Optional, List, Dict, Any
import yaml

from ..exceptions import ValidationError, format_fatal_error
from ..sheet_names import build_sheet_name_map
from .models import (
    ThermalViaArrayConfig, TemplateVia, TemplateComponentSlot, TemplateTrack,
    SpokeTemplate, ManualSpoke, Rule, ClonePlacement, Config,
)

logger = logging.getLogger(__name__)

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
    'name', 'template', 'role', 'origin_x_mm', 'origin_y_mm', 'rotation_deg',
    'nets', 'params', 'net_overrides', 'enabled',
    'anchor_ref', 'anchor_pad', 'anchor_role', 'anchor_sheet', 'anchor_cluster',
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
    anchor_cluster = data.get('anchor_cluster')

    template = data.get('template')
    role = data.get('role')
    if template is not None and role is not None:
        raise ValidationError(format_fatal_error(
            f"template и role одновременно в clone_placement {name!r}",
            [f"это два взаимоисключающих способа задать содержимое размещения — "
             f"либо готовый шаблон (template), либо однокомпонентное размещение "
             f"по роли (role), не оба сразу"]
        ))
    if template is None and role is None:
        raise ValidationError(format_fatal_error(
            f"ни template, ни role не заданы в clone_placement {name!r}",
            [f"нужен либо template: <имя из templates:>, либо role: <ROLE> для "
             f"однокомпонентного размещения без отдельного файла шаблона"]
        ))

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
        template=template,
        role=role,
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
        anchor_cluster=anchor_cluster,
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
        rule_net = rule_data.get('net')
        anchor_ref = rule_data.get('anchor_ref')
        anchor_role = rule_data.get('anchor_role')
        anchor_sheet = rule_data.get('anchor_sheet')
        anchor_cluster = rule_data.get('anchor_cluster')

        if anchor_ref and anchor_role:
            raise ValidationError(format_fatal_error(
                f"anchor_ref и anchor_role одновременно в правиле (цепь {rule_net!r})",
                ["это два взаимоисключающих способа задать якорь — либо по refdes "
                 "(anchor_ref), либо по полю Role (anchor_role), не оба сразу"]
            ))
        if anchor_sheet and not anchor_role:
            raise ValidationError(format_fatal_error(
                f"anchor_sheet без anchor_role в правиле (цепь {rule_net!r})",
                ["anchor_sheet только сужает неоднозначность anchor_role, сам по "
                 "себе якорем не является"]
            ))
        if not anchor_ref and not anchor_role:
            raise ValidationError(format_fatal_error(
                f"правило (цепь {rule_net!r}) без anchor_ref/anchor_role",
                ["у правила спиц обязателен якорь — anchor_ref: <ref> (компонент, "
                 "чьи пады перечислены в spokes), либо anchor_role: <ROLE> (переживает "
                 "переименование/перенумерацию — раньше это был глобальный target_ref)"]
            ))
        spokes = [_load_manual_spoke(spoke_data) for spoke_data in rule_data.get('spokes', [])]
        rules.append(Rule(net=rule_net, spokes=spokes, anchor_ref=anchor_ref,
                          anchor_role=anchor_role, anchor_sheet=anchor_sheet,
                          anchor_cluster=anchor_cluster))

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

    registry_path = data.get('registry_path')
    track_registry_path = data.get('track_registry_path')
    if registry_path:
        registry_path = str(Path(path).parent / registry_path)
    if track_registry_path:
        track_registry_path = str(Path(path).parent / track_registry_path)

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
        registry_path=registry_path,
        track_registry_path=track_registry_path,
    )
    total_spokes = sum(len(r.spokes) for r in cfg.rules)
    logger.debug(f"Конфигурация загружена: layer={cfg.layer}, "
                 f"шаблонов={len(cfg.templates)}, правил={len(cfg.rules)}, спиц={total_spokes}, "
                 f"clone_placements={len(cfg.clone_placements)}")
    return cfg