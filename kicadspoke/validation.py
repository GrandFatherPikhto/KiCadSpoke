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

logger = logging.getLogger(__name__)


def check_templates_and_pads_exist(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    Каждая спица должна ссылаться на существующий шаблон и существующую
    площадку целевого компонента — иначе спица просто тихо пропускается
    (было бы легко не заметить опечатку в имени шаблона/номере пада).
    """
    problems = []
    target_fp = adapter.get_footprint(cfg.target_ref)
    if target_fp is None:
        raise ValidationError(format_fatal_error(
            "целевой компонент не найден",
            [f"{cfg.target_ref!r} не найден на плате — проверьте target_ref в конфиге"]
        ))

    for rule in cfg.rules:
        for spoke in rule.spokes:
            if not spoke.enabled:
                continue
            if spoke.template not in cfg.templates:
                problems.append(
                    f"спица (пад {spoke.pad}, цепь {rule.net!r}): "
                    f"шаблон {spoke.template!r} не найден в templates"
                )
                continue
            pad = adapter.get_pad_by_number(target_fp, spoke.pad)
            if pad is None:
                problems.append(
                    f"спица (шаблон {spoke.template!r}, цепь {rule.net!r}): "
                    f"у {cfg.target_ref} нет площадки {spoke.pad!r}"
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


def run_all_checks(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """Запускает все проверки по порядку — от дешёвых к более полным."""
    logger.info("Предварительные проверки конфигурации...")
    check_templates_and_pads_exist(adapter, cfg)
    check_role_pool_sufficiency(adapter, cfg)
    logger.info("Все предварительные проверки пройдены")
