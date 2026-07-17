#!/usr/bin/env python3
"""
Диагностический скрипт для проверки keepout и позиций via.
Загружает конфиг, планирует перемещения, строит keepout и выводит детальную информацию.
"""

import sys
import logging
from pathlib import Path
from decap_placer.config import load_config
from decap_placer.kicad.adapter import KiCadBoardAdapter
from decap_placer.placement.planner import PlacementPlanner
from decap_placer.placement.services.keepout_builder import KeepoutBuilder
from decap_placer.utils.units import MM

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnostic_keepout.py <config.yaml>")
        sys.exit(1)

    config_path = sys.argv[1]
    
    # 1. Загружаем конфиг
    logger.info(f"Загрузка конфига: {config_path}")
    cfg = load_config(config_path)

    # 2. Подключаемся к KiCad
    logger.info("Подключение к KiCad...")
    adapter = KiCadBoardAdapter(timeout_ms=20000)
    adapter.refresh_board()

    # 3. Создаём планировщик
    logger.info("Создание планировщика...")
    planner = PlacementPlanner(adapter, cfg)

    # 4. Планируем перемещения
    logger.info("Планирование перемещений...")
    moves = planner.plan_moves()

    # 5. Получаем список компонентов и их позиции (из planner._planned)
    planned = planner._planned  # list of (component, position, direction)
    if not planned:
        logger.error("Нет запланированных компонентов!")
        return

    logger.info(f"Запланировано {len(planned)} компонентов")

    # 6. Строим keepout
    keepout_builder = KeepoutBuilder(adapter, cfg)
    cap_refs = {component.ref for component, _, _ in planned}
    keepout = keepout_builder.build_keepout(
        target_fp=planner._target_fp,
        cap_refs=cap_refs
    )
    
    logger.info(f"Построено {len(keepout)} прямоугольников keepout")
    
    # 7. Выводим информацию о keepout
    print("\n=== KEEPOUT RECTANGLES ===")
    for i, rect in enumerate(keepout):
        print(f"  [{i}] X: {rect.min_x/MM:.3f}..{rect.max_x/MM:.3f} мм, "
              f"Y: {rect.min_y/MM:.3f}..{rect.max_y/MM:.3f} мм")

    # 8. Для каждого компонента проверяем, попадает ли его позиция в keepout
    print("\n=== COMPONENT POSITIONS vs KEEPOUT ===")
    for component, pos, direction in planned:
        # Проверяем, попадает ли позиция в любой keepout-прямоугольник
        in_keepout = False
        for rect in keepout:
            if (rect.min_x <= pos.x <= rect.max_x and
                rect.min_y <= pos.y <= rect.max_y):
                in_keepout = True
                break
        status = "INSIDE" if in_keepout else "CLEAR"
        print(f"  {component.ref:6} pos=({pos.x/MM:7.3f}, {pos.y/MM:7.3f}) мм  -> {status}")

    # 9. Проверяем via-позиции (сырые, до find_free_point)
    print("\n=== STITCHING VIA POSITIONS (before keepout shift) ===")
    via_planner = planner.via_planner
    for component, pos, direction in planned:
        via_cfg = via_planner._merge_via_config(component)
        if not via_cfg.enabled:
            continue
        # Сырые позиции via
        raw_vias = via_planner._plan_stitching_vias(pos, direction, via_cfg, component.placement)
        for i, via_pos in enumerate(raw_vias):
            in_keepout = False
            for rect in keepout:
                if (rect.min_x <= via_pos.x <= rect.max_x and
                    rect.min_y <= via_pos.y <= rect.max_y):
                    in_keepout = True
                    break
            status = "INSIDE" if in_keepout else "CLEAR"
            print(f"  {component.ref} via#{i}: ({via_pos.x/MM:7.3f}, {via_pos.y/MM:7.3f}) мм  -> {status}")

    print("\n=== DONE ===")

if __name__ == "__main__":
    main()