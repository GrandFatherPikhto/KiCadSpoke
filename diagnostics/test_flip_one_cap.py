#!/usr/bin/env python3
"""
test_flip_one_cap.py — минимальный диагностический тест "настоящего" флипа.

Контекст: простое присвоение footprint.layer = BoardLayer.BL_B_Cu меняет
только поле в данных и НЕ зеркалирует площадки/шёлкографию — визуально
компонент остаётся как будто на прежней стороне (что и увидели на
скриншоте: конденсаторы сдвинулись, но не "перевернулись").

Настоящий переворот в KiCad — это GUI-action pcbnew.InteractiveEdit.flip
(TOOL_ACTION PCB_ACTIONS::flip в исходниках KiCad, хоткей F, "Flips
selected item(s) to opposite side of board"). Через IPC он доступен как
kicad.run_action(...) — но, как и любой GUI-action, работает через ТЕКУЩЕЕ
ВЫДЕЛЕНИЕ, а не принимает объекты напрямую. Порядок:
    1. board.clear_selection()
    2. board.add_to_selection([footprint])
    3. kicad.run_action("pcbnew.InteractiveEdit.flip")
    4. board.clear_selection()

ВАЖНО: run_action() — не транзакция begin_commit/push_commit сама по
себе (это GUI-действие, KiCad сам ведёт undo для него). Как это сочетается
с последующим update_items() в той же сессии — как раз то, что этот тест
должен показать.

Запуск:
    python test_flip_one_cap.py C6
"""
import argparse
import sys
import time

import kipy
from kipy.board_types import BoardLayer


def step(label, func, *args, **kwargs):
    print(f"[...] {label}", flush=True)
    t0 = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        print(f"[OK]  {label} — {elapsed} мс — {result!r}" if result is not None
              else f"[OK]  {label} — {elapsed} мс", flush=True)
        return result
    except Exception as e:
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        print(f"[ERR] {label} — {elapsed} мс — {type(e).__name__}: {e}", flush=True)
        raise


def describe(fp):
    layer_name = "F.Cu (перед)" if fp.layer == BoardLayer.BL_F_Cu else "B.Cu (зад)" if fp.layer == BoardLayer.BL_B_Cu else str(fp.layer)
    return f"layer={layer_name}, pos=({fp.position.x/1_000_000:.3f}, {fp.position.y/1_000_000:.3f}) мм, angle={fp.orientation.degrees:.1f}°"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="refdes конденсатора для теста, например C6")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    args = ap.parse_args()

    kicad = step("kipy.KiCad(...)", kipy.KiCad, timeout_ms=args.timeout_ms)
    board = step("kicad.get_board()", kicad.get_board)

    footprints = step("board.get_footprints()", lambda: list(board.get_footprints()))
    target = next((fp for fp in footprints if fp.reference_field.text.value == args.ref), None)
    if target is None:
        sys.exit(f"[ошибка] {args.ref} не найден на плате")

    print(f"\nДо флипа:  {describe(target)}\n")

    step("board.clear_selection()", board.clear_selection)
    step("board.add_to_selection([target])", board.add_to_selection, [target])
    status = step("kicad.run_action('pcbnew.InteractiveEdit.flip')",
                   kicad.run_action, "pcbnew.InteractiveEdit.flip")
    step("board.clear_selection()", board.clear_selection)

    # Перечитываем футпринт заново — старый объект target мог не обновиться
    # локально после action, выполненного мимо update_items().
    footprints_after = step("board.get_footprints() (повторно)", lambda: list(board.get_footprints()))
    target_after = next((fp for fp in footprints_after if fp.reference_field.text.value == args.ref), None)

    print(f"\nСтатус run_action: {status}")
    print(f"После флипа: {describe(target_after) if target_after else '(не найден?!)'}\n")

    if target_after and target_after.layer == BoardLayer.BL_B_Cu:
        print("Похоже, сработало — слой реально сменился на B.Cu.")
    else:
        print("Слой НЕ сменился — action не сработал так, как ожидалось, "
              "нужно разбираться дальше (возможно, неверное имя action "
              "или флип требует иного порядка вызовов).")


if __name__ == "__main__":
    main()