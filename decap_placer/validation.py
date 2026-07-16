# decap_placer/validation.py
"""
validation.py — фатальные предварительные проверки, выполняются ДО
планирования и любых изменений на плате. При обнаружении проблемы —
ValidationError с понятным, собранным сразу по всем найденным проблемам
сообщением (не одна ошибка за прогон, а полный список).
"""
import logging
from typing import List
from .config import Config
from .kicad.adapter import KiCadBoardAdapter
from .exceptions import ValidationError

logger = logging.getLogger(__name__)


def _format_fatal(title: str, problems: List[str]) -> str:
    lines = [
        "",
        "=" * 70,
        f"  ФАТАЛЬНАЯ ОШИБКА: {title}",
        "=" * 70,
    ]
    for p in problems:
        lines.append(f"  ✗ {p}")
    lines.append("=" * 70)
    lines.append("Расстановка остановлена, плата не тронута. Исправьте конфиг и запустите заново.")
    lines.append("")
    return "\n".join(lines)


def check_duplicate_component_refs(cfg: Config) -> None:
    """
    Один и тот же ref компонента не должен встречаться в конфиге дважды
    (как component1/component2 любой спицы) — иначе он будет перемещён
    (и получит виа) более одного раза, причём итоговый результат зависит
    от порядка обработки спиц, что практически гарантированно даст не то,
    что ожидалось. Не требует живого KiCad — чистая проверка конфига.
    """
    seen = {}  # ref -> (net, pad, роль) первого вхождения
    problems = []

    for rule in cfg.rules:
        for spoke in rule.spokes:
            for role, ref in (("component1", spoke.component1_ref), ("component2", spoke.component2_ref)):
                if ref is None:
                    continue
                if ref in seen:
                    prev_net, prev_pad, prev_role = seen[ref]
                    problems.append(
                        f"{ref} используется дважды: "
                        f"[{prev_net} / пад {prev_pad} / {prev_role}] и "
                        f"[{rule.net} / пад {spoke.pad} / {role}]"
                    )
                else:
                    seen[ref] = (rule.net, spoke.pad, role)

    if problems:
        raise ValidationError(_format_fatal("компонент используется более одного раза", problems))
    logger.debug(f"Проверка дубликатов: {len(seen)} уникальных компонентов, повторов не найдено")


def check_component_nets(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    Каждый компонент спицы должен иметь площадку, реально подключённую к
    цепи rule.net (не только к GND) — иначе, скорее всего, в конфиге
    перепутан ref (например, вписан конденсатор с соседней цепи питания).
    Требует живой платы — сверяет с РЕАЛЬНЫМИ цепями компонента.
    """
    problems = []

    for rule in cfg.rules:
        for spoke in rule.spokes:
            for ref in (spoke.component1_ref, spoke.component2_ref):
                if ref is None:
                    continue
                fp = adapter.get_footprint(ref)
                if fp is None:
                    problems.append(f"{ref}: компонент не найден на плате (пад {spoke.pad}, цепь {rule.net})")
                    continue
                pads = adapter.get_footprint_pads(fp)
                nets_on_component = sorted({p.net.name for p in pads if p.net and p.net.name})
                if rule.net not in nets_on_component:
                    problems.append(
                        f"{ref}: подключён к {nets_on_component or ['(нет цепей)']}, "
                        f"но спица на паде {spoke.pad} требует {rule.net!r}"
                    )

    if problems:
        raise ValidationError(_format_fatal("конденсатор не на той цепи", problems))
    logger.debug("Проверка цепей компонентов: все совпадают с ожидаемыми")


def run_all_checks(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """Запускает все проверки по порядку — сначала дешёвые (без платы), потом требующие живого борта."""
    logger.info("Предварительные проверки конфигурации...")
    check_duplicate_component_refs(cfg)
    check_component_nets(adapter, cfg)
    logger.info("Все предварительные проверки пройдены")
