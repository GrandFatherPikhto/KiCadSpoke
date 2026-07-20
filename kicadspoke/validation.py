# kicadspoke/validation.py
"""
validation.py — фатальные предварительные проверки, выполняются ДО
планирования и любых изменений на плате. При обнаружении проблемы —
ValidationError с понятным, собранным сразу по всем найденным проблемам
сообщением (не одна ошибка за прогон, а полный список).

ИЗМЕНЕНО (KiCadSpoke 4.0): раньше проверялись явные ref в конфиге
(component1_ref/component2_ref) — их больше нет, компоненты подбираются
из ComponentPool по (реальная цепь, роль). Основная защита теперь встроена
в сам ComponentPool.pop() (фатально при нехватке), но здесь — тот же самый
подсчёт делается ЗАРАНЕЕ, чтобы увидеть все нехватки сразу, а не
останавливаться на первой попавшейся спице.
"""
import logging
from typing import List, Dict
from .config import Config
from .kicad.adapter import KiCadBoardAdapter
from .exceptions import ValidationError, format_fatal_error
from .placement.services.component_pool import ComponentPool
from .placement.services.clone_role_resolver import clone_uses_selection_mode

logger = logging.getLogger(__name__)


def check_templates_and_pads_exist(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    Каждая спица должна ссылаться на существующий шаблон и существующую
    площадку целевого компонента — иначе спица просто тихо пропускается
    (было бы легко не заметить опечатку в имени шаблона/номере пада).
    """
    problems = []
    anchors = {}
    for rule in cfg.rules:
        if rule.anchor_ref not in anchors:
            anchors[rule.anchor_ref] = adapter.get_footprint(rule.anchor_ref)
            if anchors[rule.anchor_ref] is None:
                problems.append(f"правило (цепь {rule.net!r}): якорь {rule.anchor_ref!r} "
                                f"не найден на плате")

    for rule in cfg.rules:
        target_fp = anchors.get(rule.anchor_ref)
        for spoke in rule.spokes:
            if not spoke.enabled:
                continue
            if spoke.template not in cfg.templates:
                problems.append(
                    f"спица (пад {spoke.pad}, цепь {rule.net!r}): "
                    f"шаблон {spoke.template!r} не найден в templates"
                )
                continue
            pad = adapter.get_pad_by_number(target_fp, spoke.pad) if target_fp else None
            if target_fp is not None and pad is None:
                problems.append(
                    f"спица (шаблон {spoke.template!r}, цепь {rule.net!r}): "
                    f"у {rule.anchor_ref} нет площадки {spoke.pad!r}"
                )

    if problems:
        raise ValidationError(format_fatal_error("спица ссылается на несуществующий шаблон или площадку", problems))
    logger.debug("Проверка шаблонов/падов спиц: все ссылки корректны")


def check_role_pool_sufficiency(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    Для каждой цепи правила заранее считает, сколько компонентов каждой
    роли требуется всеми её спицами, и сверяет с реальным количеством
    компонентов на плате (та же цепь + поле Role) — фатально и со списком
    всех нехваток разом, если не сходится хоть где-то.
    """
    problems = []

    for rule in cfg.rules:
        needed_counts: Dict[str, int] = {}
        for spoke in rule.spokes:
            if not spoke.enabled:
                continue
            template = cfg.templates.get(spoke.template)
            if template is None:
                continue  # уже поймано check_templates_and_pads_exist
            for slot in template.components:
                needed_counts[slot.role] = needed_counts.get(slot.role, 0) + 1

        if not needed_counts:
            continue

        pool = ComponentPool(adapter, rule.net, roles=sorted(needed_counts.keys()))
        for role, needed in needed_counts.items():
            available = pool.remaining_count(role)
            if available < needed:
                problems.append(
                    f"цепь {rule.net!r}, роль {role!r}: нужно {needed}, найдено {available} "
                    f"(проверьте поле Role в схеме и реальное подключение к цепи)"
                )

    if problems:
        raise ValidationError(format_fatal_error("не хватает компонентов для ролей шаблона", problems))
    logger.debug("Проверка достаточности пулов по ролям: всё сходится")


def check_clone_templates_exist(cfg: Config) -> None:
    """
    Каждый ClonePlacement должен ссылаться на существующий шаблон — чисто
    конфиговая проверка, живой платы не требует вообще.
    """
    problems = []
    for clone in cfg.clone_placements:
        if not clone.enabled:
            continue
        if clone.template not in cfg.templates:
            problems.append(f"clone_placements {clone.name!r}: шаблон {clone.template!r} не найден в templates")
    if problems:
        raise ValidationError(format_fatal_error("clone_placements ссылается на несуществующий шаблон", problems))
    logger.debug("Проверка шаблонов clone_placements: все ссылки корректны")


def check_single_selection_based_clone(cfg: Config) -> None:
    """
    В KiCad в любой момент активно ТОЛЬКО ОДНО выделение — значит, за один
    прогон нельзя обработать больше одного ClonePlacement в режиме "по
    выделению" (нет ни nets, ни params). Если их больше одного — фатально,
    с подсказкой либо выключить лишние (enabled: false), либо запускать
    apply отдельно на каждый через --clone-placement NAME.
    """
    selection_based = [c.name for c in cfg.clone_placements if c.enabled and clone_uses_selection_mode(c)]
    if len(selection_based) > 1:
        raise ValidationError(format_fatal_error(
            "несколько clone_placements в режиме «по выделению» в одном прогоне",
            [f"найдено {len(selection_based)}: {selection_based} — в KiCad активно только одно "
             f"выделение сразу, обработать все сразу нельзя",
             "решение: либо enabled: false у всех, кроме одного, либо запускать "
             "apply отдельно для каждого через --clone-placement NAME"]
        ))
    logger.debug("Проверка на множественное выделение в clone_placements: сходится")


def run_all_checks(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """Запускает все проверки по порядку — от дешёвых к более полным."""
    logger.info("Предварительные проверки конфигурации...")
    check_clone_templates_exist(cfg)
    check_single_selection_based_clone(cfg)
    check_templates_and_pads_exist(adapter, cfg)
    check_role_pool_sufficiency(adapter, cfg)
    logger.info("Все предварительные проверки пройдены")
