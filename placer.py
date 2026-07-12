#!/usr/bin/env python3
"""
placer.py — главный скрипт для расстановки развязывающих конденсаторов.

Использование:
    python placer.py decap_placement.yaml [--dry-run] [--timeout-ms 20000] [--batch-size 10]
    python placer.py generate --net <file.net> --pcb <file.kicad_pcb> [--output rules.yaml]
"""

import argparse
import sys
import logging
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent))

from decap_placer.config import load_config
from decap_placer.kicad.adapter import KiCadBoardAdapter
from decap_placer.placement.planner import PlacementPlanner
from decap_placer.placement.executor import BatchExecutor
from decap_placer.exceptions import PlacerError
from decap_placer.rules.generator import RulesGenerator

# --- Группы конденсаторов для генератора ---
DEFAULT_GROUPS = {
    "+3V3_VCCIO":      {"100nF": [f"C{i}" for i in range(5, 15)],   "4.7uF": [f"C{i}" for i in range(30, 38)]},
    "+1V2_VCCINT":     {"100nF": [f"C{i}" for i in range(19, 28)],  "4.7uF": [f"C{i}" for i in range(40, 47)]},
    "+2V5_VCCA":       {"100nF": ["C28", "C29"],                     "4.7uF": ["C51", "C52"]},
    "+1V2_VCCD_PLL":   {"100nF": ["C38", "C39"],                     "4.7uF": ["C53", "C54"]},
}


def setup_logging(verbose: bool = False, log_file: str = None):
    """Настройка логирования: уровень и вывод в консоль и/или файл."""
    level = logging.DEBUG if verbose else logging.INFO
    handlers = []
    # Консольный обработчик
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    handlers.append(console)

    # Файловый обработчик (если указан)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # В файл пишем всё
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        handlers.append(file_handler)

    logging.basicConfig(level=logging.DEBUG, handlers=handlers)


def cmd_apply(args):
    """Основная команда: применить расстановку."""
    logger = logging.getLogger(__name__)
    logger.info(f"Загрузка конфига: {args.config}")
    cfg = load_config(args.config)

    logger.info(f"Подключение к KiCad (таймаут {args.timeout_ms} мс)")
    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()

    logger.info("Планирование расстановки...")
    planner = PlacementPlanner(adapter, cfg)
    moves, vias = planner.plan()

    logger.info(f"Запланировано перемещений: {len(moves)}, виа: {len(vias)}")

    if args.dry_run:
        print("\n=== DRY RUN ===")
        print("Перемещения:")
        for m in moves:
            print(f"  {m.ref}: ({m.position.x/1e6:.3f}, {m.position.y/1e6:.3f}) мм, угол={m.angle.degrees:.1f}°")
        print("\nВиа:")
        for v in vias:
            print(f"  via у {v.owner_ref}: ({v.position.x/1e6:.3f}, {v.position.y/1e6:.3f}) мм")
        return

    logger.info("Применение изменений...")
    executor = BatchExecutor(adapter, cfg, batch_size=args.batch_size)
    failed_refs, failed_vias = executor.execute(
            moves, vias,
            check_collisions=not args.no_collision_check,
            collision_margin_mm=args.collision_margin
        )

    if failed_refs:
        logger.warning(f"Не удалось переместить: {sorted(set(failed_refs))}")
    if failed_vias:
        logger.warning(f"Не удалось создать виа рядом с: {sorted(set(failed_vias))}")
    if not failed_refs and not failed_vias:
        logger.info("✅ Все операции выполнены успешно")
    else:
        logger.warning("⚠️ Некоторые операции завершились с ошибками – проверьте лог.")


def cmd_generate(args):
    """Команда генерации правил."""
    logger = logging.getLogger(__name__)
    logger.info(f"Генерация правил для {args.target} из {args.net} и {args.pcb}")
    generator = RulesGenerator(
        net_path=args.net,
        pcb_path=args.pcb,
        target_ref=args.target,
        groups=DEFAULT_GROUPS,
        default_100nf_offset_mm=args.nf_offset,
        default_47uf_offset_mm=args.uf_offset,
        repeat_fan_step_mm=args.fan_step,
        min_pin_spacing_mm=args.min_spacing,
    )
    yaml_str = generator.generate_yaml()
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(yaml_str)
        logger.info(f"Правила сохранены в {args.output}")
    else:
        print(yaml_str)


def main():
    # Если первый аргумент не является подкомандой, подставляем 'apply'
    if len(sys.argv) > 1 and sys.argv[1] not in ['apply', 'generate']:
        sys.argv.insert(1, 'apply')

    parser = argparse.ArgumentParser(
        description="KiCad Decap Placer – расстановка конденсаторов и генерация правил",
        epilog="Пример: placer.py decap_placement.yaml --dry-run"
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Подкоманда")

    # Подкоманда apply
    apply_parser = subparsers.add_parser("apply", help="Применить расстановку")
    apply_parser.add_argument("config", help="YAML конфигурационный файл")
    apply_parser.add_argument("--dry-run", action="store_true", help="Только распечатать план, не применять")
    apply_parser.add_argument("--timeout-ms", type=int, default=20000, help="Таймаут IPC, мс")
    apply_parser.add_argument("--batch-size", type=int, default=10, help="Размер батча для коммитов")
    apply_parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    apply_parser.add_argument("--log-file", help="Файл для сохранения логов")

    # Подкоманда generate
    gen_parser = subparsers.add_parser("generate", help="Сгенерировать правила (YAML)")
    gen_parser.add_argument("--net", required=True, help="Путь к .net файлу")
    gen_parser.add_argument("--pcb", required=True, help="Путь к .kicad_pcb файлу")
    gen_parser.add_argument("--target", default="IC1", help="Refdes целевого компонента")
    gen_parser.add_argument("--output", "-o", help="Файл для сохранения (если не указан, печатает в stdout)")
    gen_parser.add_argument("--100nf-offset", type=float, default=1.0, help="Отступ для 100nF (inside)")
    gen_parser.add_argument("--47uf-offset", type=float, default=2.2, help="Отступ для 4.7uF (outside)")
    gen_parser.add_argument("--fan-step", type=float, default=0.9, help="Шаг при повторном использовании пина")
    gen_parser.add_argument("--min-spacing", type=float, default=2.0, help="Минимальное расстояние между пинами")
    gen_parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    gen_parser.add_argument("--log-file", help="Файл для сохранения логов")
    apply_parser.add_argument("--no-collision-check", action="store_true", help="Отключить проверку коллизий")
    apply_parser.add_argument("--collision-margin", type=float, default=0.2, help="Дополнительный зазор при проверке коллизий, мм")

    args = parser.parse_args()

    setup_logging(verbose=getattr(args, "verbose", False), log_file=getattr(args, "log_file", None))

    try:
        if args.command == "apply":
            cmd_apply(args)
        elif args.command == "generate":
            cmd_generate(args)
        else:
            parser.print_help()
            sys.exit(1)
    except PlacerError as e:
        logging.error(f"Ошибка: {e}")
        sys.exit(1)
    except Exception as e:
        logging.exception("Неожиданная ошибка")
        sys.exit(2)


if __name__ == "__main__":
    main()