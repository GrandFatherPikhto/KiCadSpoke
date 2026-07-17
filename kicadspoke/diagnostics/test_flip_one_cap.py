#!/usr/bin/env python3
"""
test_flip_one_cap.py — минимальный диагностический тест "настоящего" флипа (KiCadSpoke).

Контекст: простое присвоение footprint.layer = BoardLayer.BL_B_Cu меняет
только поле в данных и НЕ зеркалирует площадки/шёлкографию — визуально
компонент остаётся как будто на прежней стороне.

Настоящий переворот в KiCad — это GUI-action pcbnew.InteractiveEdit.flip
(TOOL_ACTION PCB_ACTIONS::flip в исходниках KiCad, хоткей F, "Flips
selected item(s) to opposite side of board"). Через IPC он доступен как
kicad.run_action(...) — но, как и любой GUI-action, работает через ТЕКУЩЕЕ
ВЫДЕЛЕНИЕ, а не принимает объекты напрямую.

Использует адаптер KiCadSpoke, который инкапсулирует flip и перечитывание.

Запуск:
    python -m kicadspoke.diagnostics.test_flip_one_cap C6
"""

import argparse
import sys
import time

from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.utils.units import MM
from kipy.board_types import BoardLayer


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


def describe(fp):
    layer_name = "F.Cu" if fp.layer == BoardLayer.BL_F_Cu else "B.Cu" if fp.layer == BoardLayer.BL_B_Cu else str(fp.layer)
    return f"layer={layer_name}, pos=({fp.position.x/1e6:.3f}, {fp.position.y/1e6:.3f}) мм, angle={fp.orientation.degrees:.1f}°"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="refdes конденсатора для теста, например C6")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    args = ap.parse_args()

    print(f"=== Тест: флип компонента {args.ref}, timeout={args.timeout_ms} мс ===\n")

    adapter = step("KiCadBoardAdapter(...)", KiCadBoardAdapter, timeout_ms=args.timeout_ms)
    step("adapter.refresh_board()", adapter.refresh_board)

    fp = step(f"adapter.get_footprint({args.ref!r})", adapter.get_footprint, args.ref)
    if fp is None:
        sys.exit(f"[ошибка] {args.ref} не найден на плате")

    print(f"\nДо флипа:  {describe(fp)}\n")

    # Используем адаптер для флипа (он делает clear_selection, add_to_selection, run_action, clear_selection)
    step("adapter.flip_selected([fp])", adapter.flip_selected, [fp])

    # После флипа локальный объект устарел — перечитываем футпринт
    step("adapter.refresh_board()", adapter.refresh_board)
    fp_after = step(f"adapter.get_footprint({args.ref!r}) (после)", adapter.get_footprint, args.ref)

    print(f"\nПосле флипа: {describe(fp_after) if fp_after else '(не найден?!)'}\n")

    if fp_after and fp_after.layer == BoardLayer.BL_B_Cu:
        print("Похоже, сработало — слой реально сменился на B.Cu.")
    else:
        print("Слой НЕ сменился — action не сработал так, как ожидалось, "
              "нужно разбираться дальше.")


if __name__ == "__main__":
    main()