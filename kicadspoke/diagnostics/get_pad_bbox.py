#!/usr/bin/env python3
"""
kicadspoke/diagnostics/get_pad_bbox.py

Диагностический скрипт для получения bounding box'а пада.
Показывает реальные размеры, которые используются для построения keepout.
"""

import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import logging
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.utils.units import MM

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Получение bounding box пада")
    parser.add_argument("--ref", default="IC1", help="Refdes целевого компонента")
    parser.add_argument("--pad", help="Номер пада (если не указан, покажет все)")
    parser.add_argument("--timeout", type=int, default=20000, help="Таймаут IPC")
    parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    adapter = KiCadBoardAdapter(timeout_ms=args.timeout)
    adapter.refresh_board()

    fp = adapter.get_footprint(args.ref)
    if fp is None:
        logger.error(f"Компонент {args.ref} не найден")
        sys.exit(1)

    pads = adapter.get_footprint_pads(fp)
    if not pads:
        logger.error(f"У {args.ref} нет падов")
        sys.exit(1)

    if args.pad:
        pads = [p for p in pads if p.number == args.pad]
        if not pads:
            logger.error(f"Пад {args.pad} не найден у {args.ref}")
            sys.exit(1)

    # Получаем bounding box'ы для всех падов одним запросом
    bboxes = adapter.get_bounding_boxes(pads)
    logger.info(f"Получено {len(bboxes)} bounding box'ов")

    for pad, bbox in zip(pads, bboxes):
        if bbox is None:
            logger.info(f"Пад {pad.number}: bbox отсутствует")
            continue
        w = bbox.size.x / MM
        h = bbox.size.y / MM
        logger.info(f"Пад {pad.number}: размер {w:.3f} x {h:.3f} мм, "
                    f"позиция ({bbox.pos.x/MM:.3f}, {bbox.pos.y/MM:.3f}) мм")

    # Если нужен отдельный пад с более детальной информацией (включая медный слой)
    if args.pad:
        pad = pads[0]
        from kicadspoke.geometry.thermal_grid import get_pad_size
        size = get_pad_size(pad)
        if size:
            logger.info(f"Медный слой пада {pad.number}: {size[0]/MM:.3f} x {size[1]/MM:.3f} мм")


if __name__ == "__main__":
    main()