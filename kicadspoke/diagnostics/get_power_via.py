#!/usr/bin/env python3
"""
kicadspoke/diagnostics/get_power_via.py

Диагностика расчёта позиции power via.
Показывает для каждого пада:
- Нормаль к границе зоны
- Точку при смещении по нормали
- Точку при смещении к центру зоны
- BBox пада
"""

import sys
import math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import logging
from kipy.geometry import Vector2

from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.geometry.boundary import closest_point_on_polygon, polyline_points
from kicadspoke.utils.units import MM
from kicadspoke.config import load_config
from kicadspoke.geometry.keepout import Rect

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Диагностика power via")
    parser.add_argument("config", help="Путь к YAML конфигу")
    parser.add_argument("--pad", help="Номер пада (если не указан, все)")
    parser.add_argument("--offset", type=float, default=1.0, help="Смещение в мм")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    cfg = load_config(args.config)
    adapter = KiCadBoardAdapter()
    adapter.refresh_board()

    target_fp = adapter.get_footprint(cfg.target_ref)
    if target_fp is None:
        logger.error(f"Компонент {cfg.target_ref} не найден")
        sys.exit(1)

    zone = adapter.get_zone_by_name(cfg.boundary_zone)
    if zone is None:
        logger.error(f"Зона {cfg.boundary_zone} не найдена")
        sys.exit(1)
    boundary_polygon = polyline_points(zone.outline.outline)

    # Центр зоны (bbox) для старой логики
    xs = [p.x for p in boundary_polygon]
    ys = [p.y for p in boundary_polygon]
    zone_center = Vector2.from_xy(int((min(xs) + max(xs)) / 2), int((min(ys) + max(ys)) / 2))

    for rule in cfg.rules:
        for spoke in rule.spokes:
            if args.pad and spoke.pad != args.pad:
                continue
            pad = adapter.get_pad_by_number(target_fp, spoke.pad)
            if pad is None:
                continue

            # Получаем bbox пада
            bboxes = adapter.get_bounding_boxes([pad])
            bbox = bboxes[0] if bboxes else None
            if bbox:
                logger.info(f"Пад {spoke.pad}: bbox {bbox.size.x/MM:.3f} x {bbox.size.y/MM:.3f} мм")

            # Нормаль к границе (через closest_point_on_polygon)
            _, (nx, ny) = closest_point_on_polygon(pad.position, boundary_polygon)
            logger.info(f"  Нормаль: ({nx:.3f}, {ny:.3f})")

            # Точка по нормали
            offset_nm = args.offset * MM
            pos_normal = Vector2.from_xy(
                int(pad.position.x + nx * offset_nm),
                int(pad.position.y + ny * offset_nm)
            )
            logger.info(f"  По нормали: ({pos_normal.x/MM:.3f}, {pos_normal.y/MM:.3f}) мм")

            # Точка по направлению к центру зоны (старая логика)
            dx = zone_center.x - pad.position.x
            dy = zone_center.y - pad.position.y
            length = math.hypot(dx, dy)
            if length > 0:
                ux, uy = dx/length, dy/length
                pos_center = Vector2.from_xy(
                    int(pad.position.x + ux * offset_nm),
                    int(pad.position.y + uy * offset_nm)
                )
                logger.info(f"  К центру: ({pos_center.x/MM:.3f}, {pos_center.y/MM:.3f}) мм")
            else:
                logger.info("  К центру: пад совпадает с центром")
                pos_center = None

            # Проверяем, свободна ли каждая точка с учётом bbox + clearance
            if bbox:
                clearance = int(cfg.via_keepout_clearance_mm * MM)
                rect = Rect.from_bbox(bbox, clearance)
                in_keepout_normal = (rect.min_x <= pos_normal.x <= rect.max_x and
                                     rect.min_y <= pos_normal.y <= rect.max_y)
                logger.info(f"  По нормали внутри keepout: {in_keepout_normal}")
                if pos_center:
                    in_keepout_center = (rect.min_x <= pos_center.x <= rect.max_x and
                                         rect.min_y <= pos_center.y <= rect.max_y)
                    logger.info(f"  К центру внутри keepout: {in_keepout_center}")

if __name__ == "__main__":
    main()