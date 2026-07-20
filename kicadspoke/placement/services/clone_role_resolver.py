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
from typing import Dict, List
from kipy.board_types import FootprintInstance

from ...config import SpokeTemplate, ClonePlacement
from ...exceptions import ValidationError, format_fatal_error
from ...net_resolution import resolve_net
from .component_pool import ROLE_FIELD_NAME

logger = logging.getLogger(__name__)


def clone_uses_selection_mode(clone: ClonePlacement) -> bool:
    """
    Режим "по выделению", если не заданы ни nets, ни params — иначе "по
    цепям". Единственное место, где принимается это решение — и
    ClonePositionCalculator, и validation.py должны спрашивать именно
    здесь, а не дублировать правило у себя.
    """
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


def resolve_roles_by_nets(adapter, template: SpokeTemplate, clone: ClonePlacement) -> Dict[str, str]:
    """
    Сопоставление по явным/параметризованным цепям (без выделения мышкой).

    Каскад разрешения неоднозначности (каждая ступень только СУЖАЕТ,
    ничего не выбирает за человека):
      0. clone.refs[role] — явный override, минуя поиск вовсе;
      1. кандидаты = Role-поле совпадает И сидит на ожидаемой цепи;
      2. если кандидатов несколько — второй проход: оставить только
         соседей по листу иерархии с уже однозначно разрешёнными ролями
         ЭТОГО ЖЕ размещения (межканальную неоднозначность на глобальных
         цепях гасит именно это: FB нашёлся по локальной цепи канала —
         C1 ищем в его же листе);
      3. всё ещё несколько — ФАТАЛ: кандидаты электрически неразличимы
         (типовой случай: три одинаковых фильтра в одном листе с
         одинаковыми ролями) — человеку предлагается либо развести роли
         по именам, либо дать явный refs.
    """
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

    # --- второй проход: сужение неоднозначных по листу иерархии соседей ---
    neighbor_sheets = {_sheet_key(fp) for fp in resolved_fps}
    neighbor_sheets.discard('')
    for role, expected_net, matched in ambiguous:
        narrowed = matched
        if neighbor_sheets:
            by_sheet = [fp for fp in matched if _sheet_key(fp) in neighbor_sheets]
            if by_sheet:
                if len(by_sheet) < len(matched):
                    logger.info(f"[{clone.name}] роль {role!r}: {len(matched)} кандидатов "
                                f"сужено до {len(by_sheet)} по листу иерархии уже "
                                f"разрешённых соседей")
                narrowed = by_sheet
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
                + f": {refs}. Выходы: развести роли по именам в схеме "
                f"(напр. DAC_PI_3V3_C1 vs DAC_PI_AVDD_C1) ЛИБО указать явно: "
                f"refs: {{{role}: {refs[0]}}}")

    if problems:
        raise ValidationError(format_fatal_error(
            f"сопоставление по цепям не сошлось ({clone.name!r})", problems
        ))

    logger.info(f"[{clone.name}] сопоставлено по цепям: {len(role_to_ref)} ролей")
    return role_to_ref
