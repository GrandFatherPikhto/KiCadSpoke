# kicadspoke/template_extraction.py
"""
template_extraction.py — извлечение шаблона спицы из текущего выделения
на плате (не из sheet_path/иерархии схемы — решили, что выделение и
надёжнее, и не зависит от того, иерархический лист или нет).

Алгоритм:
  1. Выделение (с учётом Group, см. adapter.get_selected_items())
     разбирается на футпринты и via, всё остальное игнорируется.
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

Роли (поле Role) обязаны быть уникальны внутри выделения — фатальная
ошибка при извлечении, не только при последующей загрузке шаблона.
"""
import logging
from typing import List, Dict, Any, Optional
from kipy.board_types import FootprintInstance, Via
from kipy.geometry import Vector2

from .exceptions import ValidationError, format_fatal_error
from .kicad.adapter import KiCadBoardAdapter
from .net_resolution import parametrize_net
from .utils.units import MM

logger = logging.getLogger(__name__)

ROLE_FIELD_NAME = "Role"


def _bbox_origin(footprints: List[FootprintInstance], vias: List[Via]) -> Vector2:
    """(min_x, max_y) — левый нижний угол bounding box'а всего выделения."""
    xs = [fp.position.x for fp in footprints] + [v.position.x for v in vias]
    ys = [fp.position.y for fp in footprints] + [v.position.y for v in vias]
    return Vector2.from_xy(min(xs), max(ys))


def _find_origin(footprints: List[FootprintInstance], vias: List[Via],
                 origin_via_net: Optional[str], origin_component_role: Optional[str],
                 adapter: KiCadBoardAdapter) -> Vector2:
    """
    origin по умолчанию — bbox (см. _bbox_origin). Если задан origin_via_net
    или origin_component_role — origin берётся из конкретного элемента
    выделения (его текущая позиция на плате), а не из bbox. Оба варианта
    взаимоисключающие, проверяется в kicadspoke_cli.py. Фатально, если
    элемент не найден или (для via_net) неоднозначен — никакого угадывания
    "первого попавшегося".
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
                return fp.position
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
    текущей позиции конкретного via/компонента; про опору на пад компонента
    см. обсуждение в чате — здесь принципиально не реализовано, потому что
    transform_template.py (пост-обработка без живой платы) всё равно не
    сможет её потом переиспользовать без live-доступа к падам.
    """
    params = params or {}
    net_template_map = net_template_map or {}
    items = adapter.get_selected_items()
    footprints = [i for i in items if isinstance(i, FootprintInstance)]
    vias = [i for i in items if isinstance(i, Via)]
    ignored = [i for i in items if not isinstance(i, (FootprintInstance, Via))]

    if ignored:
        logger.warning(f"{len(ignored)} выделенных объектов — не футпринт и не via, "
                       f"проигнорированы (шаблон поддерживает только их)")

    if not footprints and not vias:
        raise ValidationError(format_fatal_error(
            "нечего извлекать",
            ["Ничего не выделено (или выделены объекты, отличные от футпринтов/via) — "
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

    origin = _find_origin(footprints, vias, origin_via_net, origin_component_role, adapter)
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

        if net_template_map:
            fp_nets = sorted({p.net.name for p in adapter.get_footprint_pads(fp)
                              if p.net and p.net.name})
            mapped = [n for n in fp_nets if n in net_template_map]
            if len(mapped) == 1:
                slot["net_template"] = parametrize_net(mapped[0], net_template_map, params)
            elif len(mapped) > 1:
                logger.warning(f"  {fp.reference_field.text.value} (роль {role}): "
                               f"{len(mapped)} цепей из --net-template сразу на падах "
                               f"({mapped}) — net_template не проставлен, впиши руками "
                               f"в получившемся YAML, какая из них роль")
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

    logger.info(f"Извлечён шаблон {name!r}: {len(components)} компонентов, {len(spoke_vias)} via уровня спицы")
    result = {"vias": spoke_vias, "components": components, "layer": tpl_layer_str}
    return {name: result}