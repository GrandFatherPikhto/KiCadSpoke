#!/usr/bin/env python3
# main.py

import argparse
import sys
import logging

from decap_placer.config import load_config
from decap_placer.kicad.adapter import KiCadBoardAdapter
from decap_placer.placement.planner import PlacementPlanner
from decap_placer.placement.executor import BatchExecutor
from decap_placer.exceptions import PlacerError

def setup_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def main():
    parser = argparse.ArgumentParser(description="KiCad Decap Placer")
    parser.add_argument("config", help="YAML конфигурационный файл")
    parser.add_argument("--dry-run", action="store_true", help="Только распечатать план, не применять")
    parser.add_argument("--timeout-ms", type=int, default=20000, help="Таймаут IPC")
    parser.add_argument("--batch-size", type=int, default=10, help="Размер батча для коммитов")
    parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    args = parser.parse_args()

    setup_logging()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        # 1. Загрузка конфига
        cfg = load_config(args.config)
        logging.info(f"Конфиг загружен: target={cfg.target_ref}, side={cfg.side}")

        # 2. Адаптер
        adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
        adapter.refresh_board()
        logging.info("Подключение к KiCad установлено")

        # 3. Планировщик
        planner = PlacementPlanner(adapter, cfg)
        moves, vias = planner.plan()
        logging.info(f"Запланировано перемещений: {len(moves)}, виа: {len(vias)}")

        if args.dry_run:
            print("=== DRY RUN ===")
            for m in moves:
                print(f"  {m.ref}: ({m.position.x/1e6:.3f}, {m.position.y/1e6:.3f}) мм, угол={m.angle.degrees:.1f}°")
            for v in vias:
                print(f"  via у {v.owner_ref}: ({v.position.x/1e6:.3f}, {v.position.y/1e6:.3f}) мм")
            return

        # 4. Исполнитель
        executor = BatchExecutor(adapter, cfg, batch_size=args.batch_size)
        failed_refs, failed_vias = executor.execute(moves, vias)

        if failed_refs:
            logging.warning(f"Не удалось переместить: {sorted(set(failed_refs))}")
        if failed_vias:
            logging.warning(f"Не удалось создать виа рядом с: {sorted(set(failed_vias))}")
        if not failed_refs and not failed_vias:
            logging.info("Все операции выполнены успешно")

    except PlacerError as e:
        logging.error(str(e))
        sys.exit(1)
    except Exception as e:
        logging.exception("Неожиданная ошибка")
        sys.exit(2)

if __name__ == "__main__":
    main()