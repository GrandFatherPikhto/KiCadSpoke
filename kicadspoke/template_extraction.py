# kicadspoke/template_extraction.py
"""
template_extraction.py — извлечение шаблона спицы из текущего выделения
на плате (не из sheet_path/иерархии схемы — решили, что выделение и
надёжнее, и не зависит от того, иерархический лист или нет).

Алгоритм:
  1. Выделение (с учётом Group, см. adapter.get_selected_items())
     разбирается на футпринты, via и треки, всё остальное игнорируется.
  2. origin = левый нижний угол bounding box'а всего выделения
     (min_x, max_y) — в родных координатах KiCad это ровно то же самое,
     что и зрительно "левый нижний", поскольку Y и так растёт вниз.
  3. Каждый футпринт: along/across = его текущая позиция МИНУС origin,
     угол — как есть (текущее состояние выделения и есть "эталон при
     rotation_deg=0", никакого отдельного пересчёта не нужно).
  4. Каждая via: та же формула, но БЕЗ роли — via не имеет пользовательских
     полей вообще, поэтому автоматически определить "чья" она нельзя;
     все извлечённые via всегда попадают в vias уровня спицы (не внутрь
     конкретной роли компонента) — при необходимости пользователь может
     вручную перенести via в components[i].vias в получившемся YAML,
     это просто текст.
  5. Каждый ВЫДЕЛЕННЫЙ трек берётся в шаблон, ТОЛЬКО ЕСЛИ ОБА его конца
     совпадают (с допуском POSITION_TOLERANCE_MM) с чем-то ещё, тоже
     попавшим в выделение — падом выделенного компонента, выделенной via,
     или концом другого выделенного трека (для стыков без via). Дорожка,
     у которой хотя бы один конец уходит "в никуда" (например, длинный
     трек к +3V3, торчащий одним концом за пределы намеченного участка) —
     пропускается с warning, а не берётся как есть: KiCad может дать
     выделить такую дорожку целиком, даже если физически она выходит
     далеко за пределы того, что реально хотели скопировать.

Роли (поле Role) обязаны быть уникальны внутри выделения — фатальная
ошибка при извлечении, не только при последующей загрузке шаблона.
"""
import logging
from typing import List, Dict, Any, Optional, Tuple
from kipy.board_types import FootprintInstance, Via, Track
from kipy.geometry import Vector2

from .constants import POSITION_TOLERANCE_MM
from .exceptions import ValidationError, format_fatal_error
from .kicad.adapter import KiCadBoardAdapter
from .net_resolution import parametrize_net
from .utils.units import MM

logger = logging.getLogger(__name__)

ROLE_FIELD_NAME = "Role"


def _points_match(p1: Vector2, p2: Vector2, tol_mm: float = POSITION_TOLERANCE_MM) -> bool:
    return abs(p1.x - p2.x) / MM <= tol_mm and abs(p1.y - p2.y) / MM <= tol_mm


def _point_matches_any(point: Vector2, anchors: List[Vector2]) -> bool:
    return any(_points_match(point, a) for a in anchors)


_BBOX_EPSILON_MM = 0.001  # НЕ допуск на неточность трассировки (реальный bbox via/пада
                          # уже даёт весь нужный запас сам по себе — радиус via обычно на
                          # порядок больше любой погрешности ручной трассировки) — это чисто
                          # защита от квантования координат/плавающей точки при округлении
                          # до нм, ничего общего с "насколько криво трек воткнули"


def _inflated_boxes(adapter: KiCadBoardAdapter, items: List[Any]) -> List[Any]:
    boxes = adapter.get_bounding_boxes(items)
    for b in boxes:
        if b is not None:
            b.inflate(int(_BBOX_EPSILON_MM * MM))
    return boxes


def _point_in_box(point: Vector2, box) -> bool:
    if box is None:
        return False
    return (box.pos.x <= point.x <= box.pos.x + box.size.x
            and box.pos.y <= point.y <= box.pos.y + box.size.y)


def _filter_tracks_within_selection(
    tracks: List[Track], footprints: List[FootprintInstance], vias: List[Via],
    adapter: KiCadBoardAdapter,
) -> List[Track]:
    """
    Оставляет только треки, у которых ОБА конца совпадают с чем-то ещё
    в выделении. "Совпадают" — не точное равенство координат (KiCad не
    требует этого для электрической связности — связность это перекрытие
    меди в пределах реального пятна via/пада, а не координата до микрона;
    ручная трассировка почти никогда не попадает ровно в центр), а
    попадание конца трека в РЕАЛЬНЫЙ bounding box соответствующего via
    или пада (+небольшой технологический запас, _PAD_MATCH_MARGIN_MM).
    Для стыков трек-к-треку (без via/пада между ними) запаса нет и не
    нужно — там либо конец в конец, либо это два разных трека.
    """
    all_pads = [p for fp in footprints for p in adapter.get_footprint_pads(fp)]
    pad_boxes = _inflated_boxes(adapter, all_pads)
    via_boxes = _inflated_boxes(adapter, vias)

    def endpoint_ok(point: Vector2, this_track: Track) -> bool:
        if any(_point_in_box(point, box) for box in via_boxes):
            return True
        if any(_point_in_box(point, box) for box in pad_boxes):
            return True
        for other in tracks:
            if other is this_track:
                continue
            if _points_match(point, other.start) or _points_match(point, other.end):
                return True
        return False

    kept = []
    for t in tracks:
        start_ok = endpoint_ok(t.start, t)
        end_ok = endpoint_ok(t.end, t)
        if start_ok and end_ok:
            kept.append(t)
        else:
            missing = ("оба конца" if not start_ok and not end_ok
                      else "начало" if not start_ok else "конец")
            logger.warning(f"  трек ({t.start.x/MM:.3f},{t.start.y/MM:.3f}) -> "
                           f"({t.end.x/MM:.3f},{t.end.y/MM:.3f}) мм, net={t.net.name if t.net else None}: "
                           f"{missing} не совпадает ни с чем ещё в выделении — "
                           f"похоже, уходит за пределы намеченного участка, пропущен")
    return kept


def _bbox_origin(footprints: List[FootprintInstance], vias: List[Via]) -> Vector2:
    """(min_x, max_y) — левый нижний угол bounding box'а всего выделения."""
    xs = [fp.position.x for fp in footprints] + [v.position.x for v in vias]
    ys = [fp.position.y for fp in footprints] + [v.position.y for v in vias]
    return Vector2.from_xy(min(xs), max(ys))


def _find_origin(footprints: List[FootprintInstance], vias: List[Via],
                 origin_via_net: Optional[str], origin_component_role: Optional[str],
                 origin_component_pad: Optional[str],
                 adapter: KiCadBoardAdapter) -> Vector2:
    """
    origin по умолчанию — bbox (см. _bbox_origin). Если задан origin_via_net
    или origin_component_role — origin берётся из конкретного элемента
    выделения (его текущая позиция на плате), а не из bbox. origin_via_net
    и origin_component_role взаимоисключающие (проверяется в
    kicadspoke_cli.py). origin_component_pad — ТОЛЬКО уточнение
    origin_component_role (без него бессмысленен, фатал в kicadspoke_cli.py):
    без него origin — центр компонента этой роли, с ним — позиция
    конкретного пада (тот же принцип, что anchor_pad у ClonePlacement).
    Фатально, если элемент не найден или (для via_net) неоднозначен —
    никакого угадывания "первого попавшегося".
    """
    if origin_via_net is not None:
        candidates = [v for v in vias if v.net and v.net.name == origin_via_net]
        if not candidates:
            raise ValidationError(format_fatal_error(
                f"--origin-by-via-net {origin_via_net!r} не найден в выделении",
                [f"среди {len(vias)} выделенных via нет ни одной на цепи {origin_via_net!r}"]
            ))
        if len(candidates) > 1:
            positions = [f"({v.position.x/MM:.3f}, {v.position.y/MM:.3f})" for v in candidates]
            raise ValidationError(format_fatal_error(
                f"--origin-by-via-net {origin_via_net!r} неоднозначен",
                [f"в выделении {len(candidates)} via на этой цепи: {positions} — "
                 f"уточните выделение (оставьте в нём только одну такую via) "
                 f"или задайте origin через --origin-by-component-role"]
            ))
        return candidates[0].position

    if origin_component_role is not None:
        for fp in footprints:
            if adapter.get_field_value(fp, ROLE_FIELD_NAME) == origin_component_role:
                if origin_component_pad is None:
                    return fp.position
                pad = adapter.get_pad_by_number(fp, origin_component_pad)
                if pad is None:
                    raise ValidationError(format_fatal_error(
                        f"--origin-by-component-pad {origin_component_pad!r} не найден",
                        [f"у компонента роли {origin_component_role!r} "
                         f"({fp.reference_field.text.value}) нет площадки "
                         f"{origin_component_pad!r} — номера падов это строки, как в KiCad"]
                    ))
                return pad.position
        raise ValidationError(format_fatal_error(
            f"--origin-by-component-role {origin_component_role!r} не найден в выделении",
            [f"среди {len(footprints)} выделенных компонентов нет ни одного "
             f"с ролью {origin_component_role!r}"]
        ))

    return _bbox_origin(footprints, vias)


def extract_template_from_selection(
    adapter: KiCadBoardAdapter,
    name: str,
    params: Optional[Dict[str, Any]] = None,
    net_template_map: Optional[Dict[str, str]] = None,
    origin_via_net: Optional[str] = None,
    origin_component_role: Optional[str] = None,
    origin_component_pad: Optional[str] = None,
    net_template_role: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Строит словарь {name: {vias: [...], components: [...]}}, готовый к
    записи в YAML под ключ templates. Фатально (ValidationError) падает,
    если: ничего подходящего не выделено, у выделенного компонента нет
    поля Role, или роль повторяется дважды в выделении.

    params/net_template_map — ОБА опциональны и работают только в паре
    (см. --param/--net-template в kicadspoke_cli.py): net_template_map —
    явная карта литерал-цепь -> паттерн с {placeholder}, которую пишет
    человек один раз при extract; params — те же значения, которыми этот
    паттерн потом будет резолвиться при apply, нужны здесь ТОЛЬКО для
    верификации (см. net_resolution.parametrize_net). Без net_template_map
    поведение не меняется вообще: via.net остаётся литералом, net_template
    ролей остаётся не заполнен, как раньше.

    origin_via_net/origin_component_role — ОБА опциональны, взаимоисключающие
    (см. --origin-by-via-net/--origin-by-component-role в kicadspoke_cli.py).
    Без них origin — bbox выделения, как раньше. С ними — origin берётся из
    текущей позиции конкретного via/компонента. origin_component_pad —
    ТОЛЬКО уточнение origin_component_role (см. --origin-by-component-pad):
    без него origin — центр компонента этой роли, с ним — позиция
    конкретного пада (тот же принцип, что anchor_pad у ClonePlacement).

    net_template_role — ОПЦИОНАЛЬНО, {роль: литерал_цепи} (см.
    --net-template-role в kicadspoke_cli.py). Нужен только для компонентов
    с НЕСКОЛЬКИМИ цепями из net_template_map сразу на своих падах (дроссели/
    ферритовые бусины/предохранители на стыке двух рельсов) — для них
    авто-вывод ниже принципиально не может выбрать сам, какая из цепей
    "главная" (см. warning "N цепей из --net-template сразу на падах"), и
    без этого параметра net_template остаётся пустым до ручной правки YAML.
    Никакого угадывания и здесь: если роль есть в net_template_role, но
    указанной цепи на самом деле нет среди падов компонента — фатал, а не
    молчаливое "как-нибудь сойдёт".
    """
    params = params or {}
    net_template_role = net_template_role or {}
    net_template_map = dict(net_template_map or {})
    # Авто-вывод для простого случая: если весь литерал цепи РАВЕН значению
    # параметра целиком (не часть более длинной строки) — net_template
    # однозначно выводится из params, дублировать явным --net-template не
    # нужно. Явная запись в net_template_map (если дана) имеет приоритет —
    # это оставляет лазейку для компаунд-случая (плейсхолдер внутри более
    # длинного литерала вроде DAC{channel}_DB1), где угадать позицию
    # плейсхолдера нельзя и писать надо руками.
    for key, value in params.items():
        if value not in net_template_map:
            net_template_map[value] = f"{{{key}}}"
    items = adapter.get_selected_items()
    footprints = [i for i in items if isinstance(i, FootprintInstance)]
    vias = [i for i in items if isinstance(i, Via)]
    tracks_selected = [i for i in items if isinstance(i, Track)]
    ignored = [i for i in items if not isinstance(i, (FootprintInstance, Via, Track))]

    if ignored:
        logger.warning(f"{len(ignored)} выделенных объектов — не футпринт, не via и не трек, "
                       f"проигнорированы (шаблон поддерживает только их)")

    tracks = _filter_tracks_within_selection(tracks_selected, footprints, vias, adapter) \
        if tracks_selected else []
    if len(tracks) < len(tracks_selected):
        logger.info(f"Треков в выделении: {len(tracks_selected)}, взято в шаблон: {len(tracks)} "
                    f"(остальные уходят за пределы выделения, см. warning выше)")

    if not footprints and not vias and not tracks:
        raise ValidationError(format_fatal_error(
            "нечего извлекать",
            ["Ничего не выделено (или выделены объекты, отличные от футпринтов/via/треков) — "
             "выделите нужный участок платы в KiCad перед запуском"]
        ))

    problems: List[str] = []
    roles_seen: Dict[str, str] = {}
    for fp in footprints:
        ref = fp.reference_field.text.value
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is None:
            problems.append(f"{ref}: нет поля {ROLE_FIELD_NAME!r} — у каждого выделенного "
                            f"компонента роль обязательна для извлечения шаблона")
            continue
        if role in roles_seen:
            problems.append(f"роль {role!r} встречается дважды в выделении: "
                            f"{roles_seen[role]!r} и {ref!r} — роли обязаны быть уникальны")
            continue
        roles_seen[role] = ref

    if problems:
        raise ValidationError(format_fatal_error("проблемы в текущем выделении", problems))

    origin = _find_origin(footprints, vias, origin_via_net, origin_component_role,
                          origin_component_pad, adapter)
    origin_desc = (f"via на цепи {origin_via_net!r}" if origin_via_net
                   else f"компонент роли {origin_component_role!r}" if origin_component_role
                   else "bbox выделения (левый нижний угол)")
    logger.info(f"Origin ({origin_desc}): ({origin.x/MM:.3f}, {origin.y/MM:.3f}) мм")

    # Слои — ФАКТЫ, абсолютные: слой шаблона = преобладающий слой выделения,
    # компоненты на нём наследуют без поля, выбивающиеся получают свой
    # layer явно. Никаких относительных сторон.
    from kipy.board_types import BoardLayer
    back_count = sum(1 for fp in footprints if fp.layer == BoardLayer.BL_B_Cu)
    tpl_is_back = back_count > len(footprints) / 2
    tpl_layer_str = 'B.Cu' if tpl_is_back else 'F.Cu'
    tpl_layer = BoardLayer.BL_B_Cu if tpl_is_back else BoardLayer.BL_F_Cu
    if 0 < back_count < len(footprints):
        logger.info(f"Смешанное выделение: {back_count} на B.Cu, "
                    f"{len(footprints)-back_count} на F.Cu; слой шаблона — "
                    f"{tpl_layer_str}, у выбивающихся будет явный layer")
    logger.info(f"Слой шаблона: {tpl_layer_str}")

    components = []
    for fp in footprints:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        along_mm = round((fp.position.x - origin.x) / MM, 4)
        across_mm = round((fp.position.y - origin.y) / MM, 4)
        slot = {
            "role": role,
            "offset_along_mm": along_mm,
            "offset_across_mm": across_mm,
            "angle_deg": fp.orientation.degrees,
        }
        if fp.layer != tpl_layer:
            slot["layer"] = 'F.Cu' if fp.layer == BoardLayer.BL_F_Cu else 'B.Cu'

        if role in net_template_role:
            literal = net_template_role[role]
            fp_nets = sorted({p.net.name for p in adapter.get_footprint_pads(fp)
                              if p.net and p.net.name})
            if literal not in fp_nets:
                raise ValidationError(format_fatal_error(
                    f"--net-template-role для роли {role!r} просит цепь {literal!r}, "
                    f"а её нет ни на одном паде {fp.reference_field.text.value}",
                    [f"реальные цепи на падах: {fp_nets} — проверьте опечатку в "
                     f"--net-template-role или в самой роли"]
                ))
            if literal not in net_template_map:
                raise ValidationError(format_fatal_error(
                    f"--net-template-role для роли {role!r} просит цепь {literal!r}, "
                    f"которой нет в net_template_map",
                    [f"добавьте {literal!r} в --net-template/net_template (или в params, "
                     f"если он равен значению параметра целиком) — иначе не из чего строить паттерн"]
                ))
            slot["net_template"] = parametrize_net(literal, net_template_map, params)
        elif net_template_map:
            fp_nets = sorted({p.net.name for p in adapter.get_footprint_pads(fp)
                              if p.net and p.net.name})
            mapped = [n for n in fp_nets if n in net_template_map]
            if len(mapped) == 1:
                slot["net_template"] = parametrize_net(mapped[0], net_template_map, params)
            elif len(mapped) > 1:
                logger.warning(f"  {fp.reference_field.text.value} (роль {role}): "
                               f"{len(mapped)} цепей из --net-template сразу на падах "
                               f"({mapped}) — net_template не проставлен, впиши руками в "
                               f"получившемся YAML, какая из них роль, либо задай "
                               f"--net-template-role {role}=<цепь> заранее")
        components.append(slot)
        logger.debug(f"  {fp.reference_field.text.value} (роль {role}): "
                    f"along={along_mm}, across={across_mm}, angle={fp.orientation.degrees}"
                    + (f", layer={slot.get('layer')}" if 'layer' in slot else "")
                    + (f", net_template={slot.get('net_template')}" if 'net_template' in slot else ""))

    spoke_vias = []
    for v in vias:
        along_mm = round((v.position.x - origin.x) / MM, 4)
        across_mm = round((v.position.y - origin.y) / MM, 4)
        via_net = v.net.name if v.net else None
        if via_net is not None and net_template_map:
            via_net = parametrize_net(via_net, net_template_map, params)
        spoke_vias.append({
            "offset_along_mm": along_mm,
            "offset_across_mm": across_mm,
            "net": via_net,
            "drill_mm": round(v.drill_diameter / MM, 4),
            "diameter_mm": round(v.diameter / MM, 4),
        })
        logger.debug(f"  via: along={along_mm}, across={across_mm}, net={via_net}")

    spoke_tracks = []
    for t in tracks:
        start_along_mm = round((t.start.x - origin.x) / MM, 4)
        start_across_mm = round((t.start.y - origin.y) / MM, 4)
        end_along_mm = round((t.end.x - origin.x) / MM, 4)
        end_across_mm = round((t.end.y - origin.y) / MM, 4)
        track_net = t.net.name if t.net else None
        if track_net is not None and net_template_map:
            track_net = parametrize_net(track_net, net_template_map, params)
        entry = {
            "start_along_mm": start_along_mm,
            "start_across_mm": start_across_mm,
            "end_along_mm": end_along_mm,
            "end_across_mm": end_across_mm,
            "width_mm": round(t.width / MM, 4),
            "net": track_net,
        }
        if t.layer != tpl_layer:
            entry["layer"] = 'F.Cu' if t.layer == BoardLayer.BL_F_Cu else 'B.Cu'
        spoke_tracks.append(entry)
        logger.debug(f"  track: ({start_along_mm},{start_across_mm}) -> "
                    f"({end_along_mm},{end_across_mm}), net={track_net}"
                    + (f", layer={entry.get('layer')}" if 'layer' in entry else ""))

    logger.info(f"Извлечён шаблон {name!r}: {len(components)} компонентов, "
               f"{len(spoke_vias)} via уровня спицы, {len(spoke_tracks)} треков")
    result = {"vias": spoke_vias, "components": components, "tracks": spoke_tracks, "layer": tpl_layer_str}
    return {name: result}