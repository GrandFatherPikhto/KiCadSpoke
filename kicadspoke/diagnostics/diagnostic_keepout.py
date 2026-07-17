#!/usr/bin/env python3
"""
diagnostic_keepout.py — диагностика keepout и позиций via (KiCadSpoke).

Загружает конфиг, планирует перемещения, строит keepout и выводит детальную информацию.
Использует новый API KiCadSpoke.

Запуск:
    python diagnostic_keepout.py <config.yaml>
"""

import sys
import logging
from pathlib import Path

# Добавляем корень проекта в sys.path, если запускается из корня
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadspoke.config import load_config
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.placement.planner import PlacementPlanner
from kicadspoke.geometry.keepout import build_keepout
from kicadspoke.utils.units import MM

logger = logging.getLogger(__name__)


def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnostic_keepout.py <config.yaml>")
        sys.exit(1)

    config_path = sys.argv[1]

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    logger.info(f"Загрузка конфига: {config_path}")
    cfg = load_config(config_path)

    logger.info("Подключение к KiCad...")
    adapter = KiCadBoardAdapter()
    adapter.refresh_board()

    logger.info("Создание планировщика...")
    planner = PlacementPlanner(adapter, cfg)

    # Планируем перемещения (это заполнит _planned и _planned_vias)
    moves = planner.plan_moves()
    planned_components = planner._planned  # список PlacedComponentInfo
    planned_vias = planner._planned_vias   # список ViaCommand (все via, кроме термовиа)

    if not planned_components and not planned_vias:
        logger.error("Нет запланированных компонентов или via!")
        return

    # Строим keepout из падов IC и компонентов (для диагностики)
    target_fp = adapter.get_footprint(cfg.target_ref)
    keepout_rects = planner.via_planner._build_keepout(target_fp, planned_components)
    # Также можно добавить уже существующие via в keepout? Но для диагностики падов достаточно.

    logger.info(f"Построено {len(keepout_rects)} прямоугольников keepout")

    # Выводим информацию о keepout
    print("\n=== KEEPOUT RECTANGLES ===")
    for i, rect in enumerate(keepout_rects):
        print(f"  [{i}] X: {rect.min_x/MM:.3f}..{rect.max_x/MM:.3f} мм, "
              f"Y: {rect.min_y/MM:.3f}..{rect.max_y/MM:.3f} мм")

    # Проверяем позиции компонентов относительно keepout
    print("\n=== COMPONENT POSITIONS vs KEEPOUT ===")
    for info in planned_components:
        pos = info.dest
        in_keepout = False
        for rect in keepout_rects:
            if (rect.min_x <= pos.x <= rect.max_x and
                rect.min_y <= pos.y <= rect.max_y):
                in_keepout = True
                break
        status = "INSIDE" if in_keepout else "CLEAR"
        print(f"  {info.ref:6} pos=({pos.x/MM:7.3f}, {pos.y/MM:7.3f}) мм  -> {status}")

    # Проверяем позиции via (уровня спицы и компонента)
    print("\n=== VIA POSITIONS vs KEEPOUT ===")
    for via_cmd in planned_vias:
        pos = via_cmd.position
        in_keepout = False
        for rect in keepout_rects:
            if (rect.min_x <= pos.x <= rect.max_x and
                rect.min_y <= pos.y <= rect.max_y):
                in_keepout = True
                break
        status = "INSIDE" if in_keepout else "CLEAR"
        print(f"  via for {via_cmd.owner_ref:6} ({pos.x/MM:7.3f}, {pos.y/MM:7.3f}) мм  -> {status}")

    # Дополнительно: термовиа (если включены) – их пока нет в planned_vias, нужно отдельно
    # Но для полноты можно вызвать planner.plan_vias() и показать термовиа, но они могут быть
    # сдвинуты из-за keepout; пока оставим.

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()