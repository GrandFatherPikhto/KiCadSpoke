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


def resolve_roles_by_nets(adapter, template: SpokeTemplate, clone: ClonePlacement) -> Dict[str, str]:
    """Сопоставление по явным/параметризованным цепям (без выделения мышкой)."""
    all_fps = adapter.get_footprints()
    fps_by_role: Dict[str, list] = {}
    for fp in all_fps:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is not None:
            fps_by_role.setdefault(role, []).append(fp)

    role_to_ref: Dict[str, str] = {}
    problems: List[str] = []

    for slot in template.components:
        role = slot.role

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

        if not matched:
            problems.append(f"роль {role!r}: не найден компонент на цепи {expected_net!r}")
        elif len(matched) > 1:
            refs = sorted(fp.reference_field.text.value for fp in matched)
            problems.append(f"роль {role!r}: неоднозначность — несколько компонентов "
                            f"на цепи {expected_net!r}: {refs}")
        else:
            role_to_ref[role] = matched[0].reference_field.text.value

    if problems:
        raise ValidationError(format_fatal_error(
            f"сопоставление по цепям не сошлось ({clone.name!r})", problems
        ))

    logger.info(f"[{clone.name}] сопоставлено по цепям: {len(role_to_ref)} ролей")
    return role_to_ref
