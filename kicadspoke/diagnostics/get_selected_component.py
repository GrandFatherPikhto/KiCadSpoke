#!/usr/bin/env python3
"""
diagnostics/get_selected_component.py — выводит детальную информацию
о выделенных на плате компонентах (refdes, номинал, футпринт, позиция,
угол, размер, пады, цепи, поле Role).

Запуск: выделите компоненты в KiCad и выполните:
    python -m kicadspoke.diagnostics.get_selected_component
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.utils.units import MM

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()

    # Получаем выделенные объекты (с учётом групп)
    items = adapter.get_selected_items()
    footprints = [i for i in items if hasattr(i, "reference_field")]  # FootprintInstance

    if not footprints:
        print("В KiCad ничего не выделено (или выделены не компоненты).")
        return

    # Получаем bounding box'ы для всех футпринтов одним батч-запросом
    bboxes = adapter.get_bounding_boxes(footprints)

    print(f"Выделено компонентов: {len(footprints)}\n")
    print("=" * 100)

    for fp, bbox in zip(footprints, bboxes):
        ref = fp.reference_field.text.value
        val = fp.value_field.text.value
        fp_name = str(fp.definition.id)
        x = fp.position.x / MM
        y = fp.position.y / MM
        angle = fp.orientation.degrees
        role = adapter.get_field_value(fp, "Role") or "(не задано)"

        if bbox:
            w = bbox.size.x / MM
            h = bbox.size.y / MM
            size_str = f"{w:.3f} x {h:.3f} мм"
        else:
            size_str = "? (bbox недоступен)"

        print(f"[{ref}]")
        print(f"  Value:        {val}")
        print(f"  Footprint:    {fp_name}")
        print(f"  Position:     ({x:.3f}, {y:.3f}) мм")
        print(f"  Angle:        {angle:.1f}°")
        print(f"  Size:         {size_str}")
        print(f"  Role:         {role}")

        # Пады
        pads = adapter.get_footprint_pads(fp)
        if pads:
            print("  Pads:")
            for pad in pads:
                pnum = pad.number
                net = pad.net.name if pad.net else "(none)"
                px = pad.position.x / MM
                py = pad.position.y / MM
                # размер пада (медный слой)
                copper = pad.padstack.copper_layers
                if copper:
                    pw = copper[0].size.x / MM
                    ph = copper[0].size.y / MM
                    psize = f"{pw:.2f} x {ph:.2f} мм"
                else:
                    psize = "?x?"
                print(f"    {pnum}: net={net:<15} pos=({px:.3f}, {py:.3f}) мм size={psize}")
        print()

    print("=" * 100)


if __name__ == "__main__":
    main()