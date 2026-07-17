#!/usr/bin/env python3
"""
get_selection.py — минимальный диагностический скрипт: печатает список
выделенных на плате элементов (компоненты, пады, треки, виа — всё, что
попало в текущее выделение в KiCad).

Запуск: выделите что-нибудь в PCB-редакторе, затем
    python get_selection.py
"""
import time

import kipy
from kipy.board_types import FootprintInstance, Pad, Track, Via, BoardLayer

MM = 1_000_000


def step(label, func, *args, **kwargs):
    print(f"[...] {label}", flush=True)
    t0 = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        print(f"[OK]  {label} — {elapsed} мс", flush=True)
        return result
    except Exception as e:
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        print(f"[ERR] {label} — {elapsed} мс — {type(e).__name__}: {e}", flush=True)
        raise


def layer_name(layer):
    if layer == BoardLayer.BL_F_Cu:
        return "F.Cu"
    if layer == BoardLayer.BL_B_Cu:
        return "B.Cu"
    return str(layer)


def describe_item(item):
    if isinstance(item, FootprintInstance):
        ref = item.reference_field.text.value
        return (f"FootprintInstance  ref={ref}  layer={layer_name(item.layer)}  "
                f"pos=({item.position.x/MM:.3f}, {item.position.y/MM:.3f}) мм  "
                f"angle={item.orientation.degrees:.1f}°")
    if isinstance(item, Pad):
        net_name = item.net.name if item.net else "?"
        return (f"Pad  number={item.number}  net={net_name}  "
                f"pos=({item.position.x/MM:.3f}, {item.position.y/MM:.3f}) мм")
    if isinstance(item, Track):
        net_name = item.net.name if item.net else "?"
        return f"Track  net={net_name}  layer={layer_name(item.layer)}"
    if isinstance(item, Via):
        net_name = item.net.name if item.net else "?"
        return (f"Via  net={net_name}  "
                f"pos=({item.position.x/MM:.3f}, {item.position.y/MM:.3f}) мм")
    return f"{type(item).__name__}: {item!r}"


def main():
    kicad = step("kipy.KiCad(...)", kipy.KiCad, timeout_ms=20000)
    board = step("kicad.get_board()", kicad.get_board)
    selection = step("board.get_selection()", board.get_selection)

    print(f"\nВыделено элементов: {len(selection)}\n")
    for item in selection:
        print(" ", describe_item(item))

    footprints = [i for i in selection if isinstance(i, FootprintInstance)]
    if footprints:
        refs = [fp.reference_field.text.value for fp in footprints]
        print(f"\nИз них компонентов (FootprintInstance): {len(footprints)}")
        print("  ", ", ".join(refs))


if __name__ == "__main__":
    main()
