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
from ...constants import CLUSTER_FIELD_NAME

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
    """
    Сопоставление по текущему выделению на плате — но выделение
    обязательно ТОЛЬКО когда роль реально неоднозначна. Если роли нет в
    выделении, но она уникальна на ВСЕЙ плате — резолвим напрямую, без
    выделения (нет смысла требовать мышку ради того, что и так однозначно).
    Выделение имеет приоритет, если роль в нём есть — компонент из
    выделения побеждает над глобальным поиском, даже если он там не один.
    """
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
    if missing:
        # Роли нет в выделении — но, может, она и не нуждается в выделении:
        # если на ВСЕЙ плате она встречается ровно один раз, резолвим
        # напрямую. Требовать мышку имеет смысл только при реальной
        # неоднозначности, не как формальность.
        all_fps_by_role: Dict[str, list] = {}
        for fp in adapter.get_footprints():
            role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
            if role in missing:
                all_fps_by_role.setdefault(role, []).append(fp)

        for role in sorted(missing):
            candidates = all_fps_by_role.get(role, [])
            if len(candidates) == 1:
                ref = candidates[0].reference_field.text.value
                role_to_ref[role] = ref
                logger.info(f"[{clone_name}] роль {role!r} -> {ref} (уникальна на всей "
                           f"плате, выделение не потребовалось)")
            elif not candidates:
                problems.append(f"роль {role!r} есть в шаблоне, но не найдена нигде на плате")
            else:
                refs = sorted(fp.reference_field.text.value for fp in candidates)
                problems.append(f"роль {role!r} есть в шаблоне, не найдена в выделении, "
                                f"и на плате неоднозначна ({len(candidates)} кандидатов: "
                                f"{refs}) — выдели нужный экземпляр на плате перед запуском")

    if problems:
        raise ValidationError(format_fatal_error(
            f"выделение не совпадает с составом шаблона ({clone_name!r})", problems
        ))

    logger.info(f"[{clone_name}] сопоставлено по выделению: {len(role_to_ref)} ролей")
    return role_to_ref


def _cluster_prefix_match(candidate_cluster: str, wanted: str) -> bool:
    """
    candidate_cluster == wanted, ИЛИ candidate_cluster начинается с
    'wanted/' — сравнение по сегментам префикса, не по подстроке (чтобы
    'Channel_1' не совпал случайно с 'Channel_10'). Плоские имена без '/'
    просто вырождаются в точное совпадение — иерархия не обязательна,
    работает тем же кодом.
    """
    return candidate_cluster == wanted or candidate_cluster.startswith(wanted + '/')


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
      2. если кандидатов несколько И задан clone.anchor_cluster — сузить
         до кандидатов, чьё поле Cluster совпадает с ним по префиксу
         сегментов (см. _cluster_prefix_match). Это и есть основной путь
         для типового случая "N одинаковых ролей на одном листе, потому
         что цепь общая силовая, а не по-канальная" — раньше здесь стояла
         попытка сузить по sheet_path (см. историю: эмпирически
         подтверждено, что UUID в sheet_path.path уникален per-компонент,
         группировки по листу не даёт вообще — ступень была тихим no-op).
      3. всё ещё несколько — сузить до пересечения с ТЕКУЩИМ выделением
         на плате, если оно не пусто и сужает хоть что-то.
      4. всё ещё несколько, и задан anchor_position — сузить по физической
         близости к якорю ЭТОГО clone_placement: ближайший кандидат
         побеждает, но только с явным отрывом (ближайший минимум вдвое
         ближе второго) — иначе это не решение, а монетка, фатал. Не
         зависит ни от refdes, ни от листа/цепи — переживает реаннотацию.
      5. всё ещё несколько — ФАТАЛ: кандидаты неразличимы всеми
         доступными способами, человеку предлагается либо развести роли
         по именам в схеме, либо задать anchor_cluster, либо выделить
         нужный экземпляр, либо (крайняя мера) явный refs.
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

    # --- сужение неоднозначных: сначала Cluster, потом выделение, потом физическая близость ---
    for role, expected_net, matched in ambiguous:
        narrowed = matched

        if clone.anchor_cluster:
            by_cluster = [fp for fp in narrowed
                         if _cluster_prefix_match(
                             adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or '',
                             clone.anchor_cluster)]
            if by_cluster and len(by_cluster) < len(narrowed):
                logger.info(f"[{clone.name}] роль {role!r}: {len(narrowed)} кандидатов "
                            f"сужено до {len(by_cluster)} по anchor_cluster "
                            f"{clone.anchor_cluster!r}")
                narrowed = by_cluster

        if len(narrowed) > 1 and selected_refs:
            by_selection = [fp for fp in narrowed
                            if fp.reference_field.text.value in selected_refs]
            if by_selection and len(by_selection) < len(narrowed):
                logger.info(f"[{clone.name}] роль {role!r}: {len(narrowed)} кандидатов "
                            f"сужено до {len(by_selection)} по текущему выделению на плате")
                narrowed = by_selection

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
        else:
            refs = sorted(fp.reference_field.text.value for fp in narrowed)
            problems.append(
                f"роль {role!r}: неоднозначность — {len(narrowed)} компонентов на цепи "
                f"{expected_net!r}"
                + (f" (уже сужено по anchor_cluster {clone.anchor_cluster!r}, но "
                   f"этого недостаточно)" if clone.anchor_cluster else
                   " (Cluster не задан — если у этих компонентов разные физические "
                   "экземпляры, anchor_cluster сузил бы до одного)")
                + by_distance_note
                + f": {refs}. Выходы: задайте anchor_cluster (если поле Cluster "
                f"проставлено в схеме), ЛИБО выделите нужный экземпляр целиком на "
                f"плате перед запуском, ЛИБО разведите роли по именам в схеме "
                f"(напр. DAC_PI_3V3_C1 vs DAC_PI_AVDD_C1), ЛИБО укажите явно: "
                f"refs: {{{role}: {refs[0]}}}")

    if problems:
        raise ValidationError(format_fatal_error(
            f"сопоставление по цепям не сошлось ({clone.name!r})", problems
        ))

    logger.info(f"[{clone.name}] сопоставлено по цепям: {len(role_to_ref)} ролей")
    return role_to_ref


def _fp_on_sheet(fp, anchor_sheet: str, sheet_names: Dict[str, str]) -> bool:
    """
    anchor_sheet встречается как ОДИН ИЗ СЕГМЕНТОВ человекочитаемого пути
    fp (не обязательно последним — компонент может быть глубже указанного
    листа). Путь строится через sheet_names (см. kicadspoke/sheet_names.py) —
    прямой парсинг .kicad_sch, эмпирически подтверждённый на реальном
    проекте (0 конфликтов, 0 нерасшифрованных uuid на mishin-coil).
    Работает для ЛЮБОГО компонента — в отличие от прежнего варианта через
    имена локальных цепей, не требует, чтобы сам fp касался локальной
    метки.
    """
    from ...sheet_names import resolve_sheet_path_names
    names = resolve_sheet_path_names(fp, sheet_names)
    return anchor_sheet in names


def resolve_anchor_by_role(adapter, clone: ClonePlacement, sheet_names: Dict[str, str]) -> FootprintInstance:
    """
    Резолв якорного компонента clone_placement по anchor_role (поле Role
    на плате, НЕ роль шаблона — это разные вещи: тут ищем сам якорь среди
    ВСЕХ футпринтов платы, а не роли внутри клонируемого шаблона). Тот же
    каскад сужения неоднозначности, что и у ролей шаблона:

      1. кандидаты = все футпринты с Role == clone.anchor_role.
      2. несколько — сузить до anchor_sheet (если задан): человекочитаемый
         путь fp (через sheet_names, см. kicadspoke/sheet_names.py)
         содержит этот сегмент (см. _fp_on_sheet).
      2b. всё ещё несколько — сузить до anchor_cluster (если задан):
          поле Cluster совпадает по префиксу сегментов (см.
          _cluster_prefix_match) — независимый от anchor_sheet канал
          сужения, читается из схемы, не из UUID/sheet_path.
      3. всё ещё несколько — сузить до текущего выделения на плате.
      4. всё ещё несколько, или 0 — ФАТАЛ со списком кандидатов и
         подсказкой (anchor_sheet/anchor_cluster/выделение/явный anchor_ref).

    sheet_names — {uuid: Sheetname}, см. Config.sheet_names; пустой словарь
    (schematic_dir/schematic_files не заданы) — anchor_sheet тогда никогда
    ничего не сузит (фатал проверяет это заранее, см. validation.py).
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
        by_sheet = [fp for fp in narrowed if _fp_on_sheet(fp, clone.anchor_sheet, sheet_names)]
        if by_sheet:
            if len(by_sheet) < len(narrowed):
                logger.info(f"[{clone.name}] anchor_role {clone.anchor_role!r}: "
                            f"{len(narrowed)} кандидатов сужено до {len(by_sheet)} "
                            f"по anchor_sheet {clone.anchor_sheet!r}")
            narrowed = by_sheet

    if len(narrowed) > 1 and clone.anchor_cluster:
        by_cluster = [fp for fp in narrowed
                     if _cluster_prefix_match(
                         adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or '',
                         clone.anchor_cluster)]
        if by_cluster:
            if len(by_cluster) < len(narrowed):
                logger.info(f"[{clone.name}] anchor_role {clone.anchor_role!r}: "
                            f"{len(narrowed)} кандидатов сужено до {len(by_cluster)} "
                            f"по anchor_cluster {clone.anchor_cluster!r}")
            narrowed = by_cluster

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
         f"и/или anchor_cluster, ЛИБО выдели нужный экземпляр на плате перед "
         f"запуском, ЛИБО укажи явно anchor_ref вместо anchor_role: {refs[0]!r}"]
    ))