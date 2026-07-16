#!/usr/bin/env python3
"""
placer.py — главный скрипт для расстановки развязывающих конденсаторов.

Использование:
    python placer.py decap_placement.yaml [--dry-run] [--timeout-ms 20000] [--batch-size 10]
    python placer.py undo [--verbose]
"""

import argparse
import sys
import logging
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent))

from kicadspoke.config import load_config
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.placement.planner import PlacementPlanner
from kicadspoke.placement.executor import BatchExecutor
from kicadspoke.exceptions import PlacerError
from kicadspoke.undo import undo_last_operation
from kicadspoke.validation import run_all_checks
from kicadspoke.registry import PlacementRegistry, registry_path_for_config


def setup_logging(verbose: bool = False, log_file: str = None):
    """Настройка логирования: уровень и вывод в консоль и/или файл."""
    level = logging.DEBUG if verbose else logging.INFO
    handlers = []
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    handlers.append(console)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
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

    run_all_checks(adapter, cfg)

    logger.info("Планирование расстановки...")
    planner = PlacementPlanner(adapter, cfg)

    if args.dry_run:
        # Via больше НЕ зависят от живого пада компонента (обобщённые via
        # — чистая геометрия от нуля спицы, см. geometry/spoke_layout.py)
        # — поэтому, в отличие от прежних версий, здесь их можно честно
        # показать. Единственная оговорка: keepout для термовиа всё ещё
        # смотрит на ТЕКУЩИЕ (ещё не перемещённые в dry-run) позиции
        # футпринтов — термовиа в dry-run могут отличаться от боевого
        # прогона, если конденсаторы реально сдвинутся с текущих мест.
        moves = planner.plan_moves()
        vias = planner.plan_vias()
        print("\n=== DRY RUN ===")
        print("Перемещения:")
        for m in moves:
            print(f"  {m.ref}: ({m.position.x/1e6:.3f}, {m.position.y/1e6:.3f}) мм, угол={m.angle.degrees:.1f}°")
        print("\nВиа:")
        for v in vias:
            print(f"  via у {v.owner_ref}: ({v.position.x/1e6:.3f}, {v.position.y/1e6:.3f}) мм, net={v.net_name}")
        print("\n(keepout термовиа посчитан по ТЕКУЩИМ позициям конденсаторов, "
              "не по целевым — может слегка отличаться от боевого прогона)")
        return

    executor = BatchExecutor(adapter, cfg, batch_size=args.batch_size)
    registry = PlacementRegistry(adapter, registry_path_for_config(args.config))

    # --- Фаза 1: перемещения ---
    moves = planner.plan_moves()
    logger.info(f"Запланировано перемещений: {len(moves)}")
    logger.info("Применение перемещений...")
    failed_refs = executor.execute_moves(
        moves,
        check_collisions=not args.no_collision_check,
        collision_margin_mm=args.collision_margin,
    )
    if failed_refs:
        logger.warning(f"Не удалось переместить: {sorted(set(failed_refs))}")

    # --- Перечитываем плату: термовиа (единственное, что по-прежнему
    # зависит от живой платы) планируются по РЕАЛЬНЫМ, уже закоммиченным
    # позициям, а не по расчётным "на бумаге" ---
    logger.info("Обновление данных платы перед планированием виа...")
    adapter.refresh_board()

    # --- Фаза 2: виа ---
    all_vias = planner.plan_vias()
    vias_to_create = registry.reconcile(all_vias)
    logger.info(f"Запланировано виа: {len(all_vias)}, из них реально к созданию "
               f"(реестр отсеял уже стоящие правильно): {len(vias_to_create)}")
    logger.info("Применение виа...")
    failed_vias = executor.execute_vias(vias_to_create, registry=registry)
    if failed_vias:
        logger.warning(f"Не удалось создать виа рядом с: {sorted(set(failed_vias))}")

    if not failed_refs and not failed_vias:
        logger.info("✅ Все операции выполнены успешно")
    else:
        logger.warning("⚠️ Некоторые операции завершились с ошибками – проверьте лог.")


def cmd_undo(args):
    """Откатывает последнюю операцию."""
    logger = logging.getLogger(__name__)
    log_dir = Path("logs")
    if not log_dir.exists():
        logger.error("Папка logs не найдена.")
        return

    files = sorted(log_dir.glob("operation_*.json"), key=lambda p: p.stat().st_ctime)
    if not files:
        logger.error("Нет файлов операций для отката.")
        return

    last_file = files[-1]
    logger.info(f"Откат операции из {last_file.name}")
    success = undo_last_operation(last_file)
    if success:
        logger.info("✅ Операция успешно откатана.")
    else:
        logger.error("❌ Не удалось откатить операцию.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ['apply', 'undo']:
        sys.argv.insert(1, 'apply')

    parser = argparse.ArgumentParser(
        description="KiCad Decap Placer – расстановка конденсаторов (ручная стратегия)",
        epilog="Пример: placer.py decap_placement.yaml --dry-run"
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Подкоманда")

    apply_parser = subparsers.add_parser("apply", help="Применить расстановку")
    apply_parser.add_argument("config", help="YAML конфигурационный файл")
    apply_parser.add_argument("--dry-run", action="store_true", help="Только распечатать план, не применять")
    apply_parser.add_argument("--timeout-ms", type=int, default=20000, help="Таймаут IPC, мс")
    apply_parser.add_argument("--batch-size", type=int, default=10, help="Размер батча для коммитов")
    apply_parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    apply_parser.add_argument("--log-file", help="Файл для сохранения логов")
    apply_parser.add_argument("--no-collision-check", action="store_true", help="Отключить проверку коллизий")
    apply_parser.add_argument("--collision-margin", type=float, default=0.2, help="Дополнительный зазор при проверке коллизий, мм")

    undo_parser = subparsers.add_parser("undo", help="Откатить последнюю операцию")
    undo_parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    undo_parser.add_argument("--log-file", help="Файл для сохранения логов")

    args = parser.parse_args()

    setup_logging(verbose=getattr(args, "verbose", False), log_file=getattr(args, "log_file", None))

    try:
        if args.command == "apply":
            cmd_apply(args)
        elif args.command == "undo":
            cmd_undo(args)
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