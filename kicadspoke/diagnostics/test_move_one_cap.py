#!/usr/bin/env python3
"""
test_move_one_cap.py — минимальный диагностический тест IPC-записи (KiCadSpoke).

Цель: изолировать зависание на begin_commit() до предела — взять ОДИН
конденсатор, сдвинуть его на 1мм по X, закоммитить. Если и это виснет —
проблема не в размере батча/коммита, а в чём-то более фундаментальном
(зависшая транзакция от предыдущего запуска, сломанное состояние сессии
KiCad и т.п.) — тогда точно нужен полный перезапуск KiCad.

Использует адаптер KiCadSpoke для работы с платой.

Запуск:
    python -m kicadspoke.diagnostics.test_move_one_cap C5 --delta-mm 1.0
    python -m kicadspoke.diagnostics.test_move_one_cap C5 --revert
"""

import argparse
import sys
import time

from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.utils.units import MM
from kipy.geometry import Vector2

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="refdes конденсатора для теста, например C5")
    ap.add_argument("--delta-mm", type=float, default=1.0, help="на сколько мм сдвинуть по X")
    ap.add_argument("--revert", action="store_true", help="сдвинуть в обратную сторону (вернуть)")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    args = ap.parse_args()

    delta = -args.delta_mm if args.revert else args.delta_mm

    print(f"=== Тест: сдвиг {args.ref} на {delta:+.2f} мм по X, timeout={args.timeout_ms} мс ===\n")

    adapter = step("KiCadBoardAdapter(...)", KiCadBoardAdapter, timeout_ms=args.timeout_ms)
    board = step("adapter.refresh_board()", adapter.refresh_board)

    fp = step(f"adapter.get_footprint({args.ref!r})", adapter.get_footprint, args.ref)
    if fp is None:
        sys.exit(f"[ошибка] {args.ref} не найден на плате")

    old_pos = fp.position
    new_pos = Vector2.from_xy(int(old_pos.x + delta * MM), int(old_pos.y))
    print(f"\nТекущая позиция {args.ref}: ({old_pos.x/MM:.3f}, {old_pos.y/MM:.3f}) мм")
    print(f"Новая позиция:            ({new_pos.x/MM:.3f}, {new_pos.y/MM:.3f}) мм\n")

    commit = step("adapter.begin_commit()", adapter.begin_commit)

    try:
        fp.position = new_pos
        step("adapter.update_items([fp])", adapter.update_items, [fp])
        step("adapter.push_commit(commit, ...)", adapter.push_commit, commit,
             f"test_move_one_cap: {args.ref}")
        print(f"\nГотово. {args.ref} сдвинут на {delta:+.2f} мм по X.")
        print("Чтобы вернуть обратно: python -m kicadspoke.diagnostics.test_move_one_cap "
              f"{args.ref} --delta-mm {args.delta_mm} --revert")
    except Exception:
        step("adapter.drop_commit(commit) (откат после ошибки)", adapter.drop_commit, commit)
        raise


if __name__ == "__main__":
    main()