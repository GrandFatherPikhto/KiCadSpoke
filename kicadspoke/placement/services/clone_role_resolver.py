# kicadspoke/placement/services/clone_role_resolver.py
"""
clone_role_resolver.py — сопоставление роль->ref для ClonePlacement, два
независимых механизма:

  1. По выделению (resolve_roles_by_selection) — для редких, штучных
     секций (одна MCU на плате). Пользователь выделяет мышкой компоненты
     конкретного, ещё не расставленного экземпляра. Полная симметричная
     проверка: каждая роль шаблона должна найтись в выделении РОВНО один
     раз, и наоборот — в выделении не должно быть ролей, которых нет в
     шаблоне.

  2. По цепям (resolve_roles_by_nets) — для многократно повторяющихся
     шаблонов (П-фильтры, каналы ЦАП), где выделение мышкой рискует
     перепутать одинаковые на вид экземпляры. Цепь для каждой роли:
     приоритет — явный ClonePlacement.nets[role] (буквально), иначе
     TemplateComponentSlot.net_template (с плейсхолдерами, через
     net_resolution.resolve_net). Никакого сопоставления по геометрии
     или паттерну ref — только явно заданная цепь.

Режим выбирается ДО вызова этого модуля (см. planner/orchestration):
если у ClonePlacement заданы nets или params — режим "по цепям", иначе —
"по выделению". Здесь это уже финальное, единственное решение, никакой
автоматики в выборе режима самим резолвером нет.
"""
import logging
import math
from typing import Dict, List, Optional
from kipy.board_types import FootprintInstance
from kipy.geometry import Vector2

from ...config import SpokeTemplate, ClonePlacement
from ...exceptions import ValidationError, format_fatal_error
from ...net_resolution import resolve_net
from ...utils.units import MM
from .component_pool import ROLE_FIELD_NAME

logger = logging.getLogger(__name__)


def clone_uses_selection_mode(clone: ClonePlacement) -> bool:
    """
    Режим "по выделению", если:
      - явно задан by_selection: true (приоритетно — см. ClonePlacement.
        by_selection: нужен отдельно от implicit-вывода, потому что params
        используется ТАКЖЕ для резолва плейсхолдеров via/track независимо
        от режима ролей — без явного флага заданный ради одной лишь via
        params молча переключил бы режим ролей на "по цепям"), ИЛИ
      - не заданы ни nets, ни params (старое implicit-поведение, дефолт
        для обратной совместимости).
    Единственное место, где принимается это решение — и
    ClonePositionCalculator, и validation.py должны спрашивать именно
    здесь, а не дублировать правило у себя.
    """
    if clone.by_selection:
        return True
    return not (clone.nets or clone.params)


def resolve_roles_by_selection(adapter, template: SpokeTemplate, clone_name: str) -> Dict[str, str]:
    """Сопоставление по текущему выделению на плате."""
    items = adapter.get_selected_items()
    footprints = [i for i in items if isinstance(i, FootprintInstance)]

    template_roles = {slot.role for slot in template.components}

    role_to_ref: Dict[str, str] = {}
    problems: List[str] = []

    for fp in footprints:
        ref = fp.reference_field.text.value
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is None:
            problems.append(f"{ref}: нет поля {ROLE_FIELD_NAME!r}")
            continue
        if role not in template_roles:
            problems.append(f"{ref}: роль {role!r} не встречается в шаблоне "
                            f"(роли шаблона: {sorted(template_roles)})")
            continue
        if role in role_to_ref:
            problems.append(f"роль {role!r} встречается дважды в выделении: "
                            f"{role_to_ref[role]!r} и {ref!r}")
            continue
        role_to_ref[role] = ref

    missing = template_roles - set(role_to_ref.keys())
    for role in sorted(missing):
        problems.append(f"роль {role!r} есть в шаблоне, но не найдена в выделении")

    if problems:
        raise ValidationError(format_fatal_error(
            f"выделение не совпадает с составом шаблона ({clone_name!r})", problems
        ))

    logger.info(f"[{clone_name}] сопоставлено по выделению: {len(role_to_ref)} ролей")
    return role_to_ref


def _sheet_key(fp) -> str:
    """
    Строковый ключ экземпляра листа иерархии для футпринта (для сравнения
    'соседи ли по листу'). sheet_path доступен с KiCad 9.0.3 / kipy 0.4;
    при недоступности возвращает '' — фильтр по листу тогда пропускается.
    """
    try:
        return str(fp.sheet_path.proto if hasattr(fp.sheet_path, 'proto') else fp.sheet_path)
    except Exception:
        return ''


def resolve_roles_by_nets(adapter, template: SpokeTemplate, clone: ClonePlacement,
                          anchor_position: Optional[Vector2] = None) -> Dict[str, str]:
    """
    Сопоставление по явным/параметризованным цепям (без выделения мышкой
    как ОСНОВНОГО механизма — но текущее выделение, если оно есть,
    участвует как ступень сужения неоднозначности, см. ниже).

    Каскад разрешения неоднозначности (каждая ступень только СУЖАЕТ,
    ничего не выбирает за человека):
      0. clone.refs[role] — явный override, минуя поиск вовсе. Ломается
         при реаннотации (refdes не стабилен) — крайняя мера, не основной
         путь.
      1. кандидаты = Role-поле совпадает И сидит на ожидаемой цепи.
      2. если кандидатов несколько — сузить до пересечения с ТЕКУЩИМ
         выделением на плате, если оно не пусто и сужает хоть что-то.
      3. всё ещё несколько — сузить до соседей по листу иерархии с уже
         однозначно разрешёнными ролями ЭТОГО ЖЕ размещения (не помогает,
         если у неоднозначных ролей нет отдельного сабшита на инстанс —
         типовой случай общей силовой цепи/развязки, не DAC-сигнала).
      4. всё ещё несколько, и задан anchor_position — сузить по физической
         близости к якорю ЭТОГО clone_placement: ближайший кандидат
         побеждает, но только с явным отрывом (ближайший минимум вдвое
         ближе второго) — иначе это не решение, а монетка, фатал. Не
         зависит ни от refdes, ни от листа/цепи — переживает реаннотацию.
      5. всё ещё несколько — ФАТАЛ: кандидаты неразличимы всеми
         доступными способами, человеку предлагается либо развести роли
         по именам в схеме, либо выделить нужный экземпляр, либо (крайняя
         мера) явный refs.
    """
    selected_items = adapter.get_selected_items()
    selected_refs = {i.reference_field.text.value for i in selected_items
                     if isinstance(i, FootprintInstance)}

    all_fps = adapter.get_footprints()
    fps_by_role: Dict[str, list] = {}
    fps_by_ref = {}
    for fp in all_fps:
        fps_by_ref[fp.reference_field.text.value] = fp
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is not None:
            fps_by_role.setdefault(role, []).append(fp)

    role_to_ref: Dict[str, str] = {}
    resolved_fps: List = []
    problems: List[str] = []
    ambiguous: List = []   # (role, expected_net, matched) на второй проход

    # --- ступень 0: явные refs ---
    for role, ref in clone.refs.items():
        if role not in {s.role for s in template.components}:
            problems.append(f"refs: роль {role!r} не существует в шаблоне {template.name!r}")
            continue
        fp = fps_by_ref.get(ref)
        if fp is None:
            problems.append(f"refs: компонент {ref!r} (роль {role!r}) не найден на плате")
            continue
        role_to_ref[role] = ref
        resolved_fps.append(fp)
        logger.info(f"[{clone.name}] роль {role!r} -> {ref} (явный refs)")

    # --- первый проход: однозначные по Role+цепи ---
    for slot in template.components:
        role = slot.role
        if role in role_to_ref:
            continue

        if role in clone.nets:
            net_template = clone.nets[role]
        elif slot.net_template is not None:
            net_template = slot.net_template
        else:
            problems.append(f"роль {role!r}: нет цепи для сопоставления (ни в nets "
                            f"{clone.name!r}, ни в net_template шаблона) — в режиме "
                            f"'по цепям' цепь обязательна для каждой роли")
            continue

        expected_net = resolve_net(net_template, clone.params, clone.net_overrides)

        candidates = fps_by_role.get(role, [])
        matched = []
        for fp in candidates:
            pads = adapter.get_footprint_pads(fp)
            nets_on_fp = {p.net.name for p in pads if p.net and p.net.name}
            if expected_net in nets_on_fp:
                matched.append(fp)

        if not candidates:
            problems.append(f"роль {role!r}: НИ ОДНОГО компонента с такой ролью на плате вообще "
                            f"(проверьте поле Role в схеме, и что Update PCB from Schematic выполнялся)")
        elif not matched:
            found_nets = sorted({n for fp in candidates for n in
                                 {p.net.name for p in adapter.get_footprint_pads(fp) if p.net and p.net.name}})
            refs = sorted(fp.reference_field.text.value for fp in candidates)
            problems.append(f"роль {role!r}: компонент(ы) {refs} с этой ролью на плате ЕСТЬ, "
                            f"но ни один не сидит на цепи {expected_net!r} — реально они на "
                            f"{found_nets} (проверьте params/имя цепи или подключение на схеме)")
        elif len(matched) > 1:
            ambiguous.append((role, expected_net, matched))
        else:
            role_to_ref[role] = matched[0].reference_field.text.value
            resolved_fps.append(matched[0])

    # --- сужение неоднозначных: сначала по выделению, потом по листу иерархии ---
    neighbor_sheets = {_sheet_key(fp) for fp in resolved_fps}
    neighbor_sheets.discard('')
    for role, expected_net, matched in ambiguous:
        narrowed = matched

        if selected_refs:
            by_selection = [fp for fp in narrowed
                            if fp.reference_field.text.value in selected_refs]
            if by_selection and len(by_selection) < len(narrowed):
                logger.info(f"[{clone.name}] роль {role!r}: {len(narrowed)} кандидатов "
                            f"сужено до {len(by_selection)} по текущему выделению на плате")
                narrowed = by_selection

        if len(narrowed) > 1 and neighbor_sheets:
            by_sheet = [fp for fp in narrowed if _sheet_key(fp) in neighbor_sheets]
            if by_sheet:
                if len(by_sheet) < len(narrowed):
                    logger.info(f"[{clone.name}] роль {role!r}: {len(narrowed)} кандидатов "
                                f"сужено до {len(by_sheet)} по листу иерархии уже "
                                f"разрешённых соседей")
                narrowed = by_sheet

        by_distance_note = ""
        if len(narrowed) > 1 and anchor_position is not None:
            with_dist = sorted(
                ((math.hypot((fp.position.x - anchor_position.x) / MM,
                             (fp.position.y - anchor_position.y) / MM), fp)
                 for fp in narrowed),
                key=lambda t: t[0]
            )
            closest_dist, closest_fp = with_dist[0]
            second_dist = with_dist[1][0]
            by_distance_note = (f" (ближайший к якорю {clone.name!r}: "
                                f"{closest_fp.reference_field.text.value} на "
                                f"{closest_dist:.2f} мм, второй — {second_dist:.2f} мм)")
            if second_dist >= 2 * max(closest_dist, 1e-6):
                logger.info(f"[{clone.name}] роль {role!r}: {len(narrowed)} кандидатов "
                            f"сужено до 1 по физической близости к якорю "
                            f"({closest_fp.reference_field.text.value}, {closest_dist:.2f} мм, "
                            f"второй ближайший — {second_dist:.2f} мм, отрыв достаточный)")
                narrowed = [closest_fp]
            else:
                logger.debug(f"[{clone.name}] роль {role!r}: по близости к якорю не сузить — "
                            f"{closest_dist:.2f} мм vs {second_dist:.2f} мм, отрыв недостаточный")

        if len(narrowed) == 1:
            role_to_ref[role] = narrowed[0].reference_field.text.value
            resolved_fps.append(narrowed[0])
        else:
            refs = sorted(fp.reference_field.text.value for fp in narrowed)
            problems.append(
                f"роль {role!r}: неоднозначность — {len(narrowed)} компонентов на цепи "
                f"{expected_net!r}"
                + (" (уже в одном листе иерархии — кандидаты электрически "
                   "неразличимы; типовой случай: несколько одинаковых фильтров "
                   "в одном листе с одинаковыми ролями)" if neighbor_sheets else "")
                + by_distance_note
                + f": {refs}. Выходы: выделите нужный экземпляр целиком на плате перед "
                f"запуском, ЛИБО разведите роли по именам в схеме "
                f"(напр. DAC_PI_3V3_C1 vs DAC_PI_AVDD_C1), ЛИБО укажите явно: "
                f"refs: {{{role}: {refs[0]}}}")

    if problems:
        raise ValidationError(format_fatal_error(
            f"сопоставление по цепям не сошлось ({clone.name!r})", problems
        ))

    logger.info(f"[{clone.name}] сопоставлено по цепям: {len(role_to_ref)} ролей")
    return role_to_ref


def _pad_on_sheet(adapter, fp, anchor_sheet: str) -> bool:
    """
    Хоть один пад fp сидит на локальной (иерархической) цепи, начинающейся
    с '/{anchor_sheet}/' — точный префикс по сегментам пути, не подстрока
    (см. обсуждение: '/Channel_0/' не должен совпасть с '/Channel_01/').
    Работает ТОЛЬКО через имя цепи — попытка сопоставить по sheet_path
    (UUID-цепочка) была эмпирически опровергнута (см. пробные скрипты в
    чате: UUID уникален per-компонент, группировки по листу нет вовсе).
    """
    prefix = f"/{anchor_sheet}/"
    for pad in adapter.get_footprint_pads(fp):
        if pad.net and (pad.net.name == f"/{anchor_sheet}" or pad.net.name.startswith(prefix)):
            return True
    return False


def resolve_anchor_by_role(adapter, clone: ClonePlacement) -> FootprintInstance:
    """
    Резолв якорного компонента clone_placement по anchor_role (поле Role
    на плате, НЕ роль шаблона — это разные вещи: тут ищем сам якорь среди
    ВСЕХ футпринтов платы, а не роли внутри клонируемого шаблона). Тот же
    каскад сужения неоднозначности, что и у ролей шаблона:

      1. кандидаты = все футпринты с Role == clone.anchor_role.
      2. несколько — сузить до anchor_sheet (если задан): хоть один пад
         на локальной цепи с этим префиксом (см. _pad_on_sheet).
      3. всё ещё несколько — сузить до текущего выделения на плате.
      4. всё ещё несколько, или 0 — ФАТАЛ со списком кандидатов и
         подсказкой (anchor_sheet/выделение/явный anchor_ref).
    """
    all_fps = adapter.get_footprints()
    candidates = [fp for fp in all_fps
                  if adapter.get_field_value(fp, ROLE_FIELD_NAME) == clone.anchor_role]

    if not candidates:
        raise ValidationError(format_fatal_error(
            f"{clone.name}: anchor_role {clone.anchor_role!r} не найден ни на одном компоненте платы",
            [f"проверь, что поле Role проставлено в схеме и долетело до PCB "
             f"(Update PCB from Schematic)"]
        ))

    narrowed = candidates
    if len(narrowed) > 1 and clone.anchor_sheet:
        by_sheet = [fp for fp in narrowed if _pad_on_sheet(adapter, fp, clone.anchor_sheet)]
        if by_sheet:
            if len(by_sheet) < len(narrowed):
                logger.info(f"[{clone.name}] anchor_role {clone.anchor_role!r}: "
                            f"{len(narrowed)} кандидатов сужено до {len(by_sheet)} "
                            f"по anchor_sheet {clone.anchor_sheet!r}")
            narrowed = by_sheet

    if len(narrowed) > 1:
        selected_items = adapter.get_selected_items()
        selected_refs = {i.reference_field.text.value for i in selected_items
                         if isinstance(i, FootprintInstance)}
        if selected_refs:
            by_selection = [fp for fp in narrowed
                            if fp.reference_field.text.value in selected_refs]
            if by_selection and len(by_selection) < len(narrowed):
                logger.info(f"[{clone.name}] anchor_role {clone.anchor_role!r}: "
                            f"{len(narrowed)} кандидатов сужено до {len(by_selection)} "
                            f"по текущему выделению на плате")
                narrowed = by_selection

    if len(narrowed) == 1:
        return narrowed[0]

    refs = sorted(fp.reference_field.text.value for fp in narrowed)
    raise ValidationError(format_fatal_error(
        f"{clone.name}: anchor_role {clone.anchor_role!r} неоднозначен",
        [f"кандидатов: {len(narrowed)}: {refs}. Выходы: уточни anchor_sheet "
         f"(если у кандидатов есть локальные цепи разных листов), ЛИБО выдели "
         f"нужный экземпляр на плате перед запуском, ЛИБО укажи явно anchor_ref "
         f"вместо anchor_role: {refs[0]!r}"]
    ))