#!/usr/bin/env python3
"""
test_create_one_via.py — минимальный диагностический тест create_items() (KiCadSpoke).

Цель: проверить СОЗДАНИЕ нового объекта (Via) через IPC.
Ставит одну via на GND рядом с указанным конденсатором (со сдвигом
offset-mm от ЦЕНТРА конденсатора в сторону от платы).

Использует адаптер KiCadSpoke.

Запуск:
    python -m kicadspoke.diagnostics.test_create_one_via C5 --offset-mm 1.2
    python -m kicadspoke.diagnostics.test_create_one_via --remove   # удалить последнюю созданную
"""

import argparse
import sys
import json
import time
from pathlib import Path

from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.utils.units import MM
from kipy.geometry import Vector2

STATE_FILE = Path(__file__).parent / ".last_test_via.json"


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
    ap.add_argument("ref", nargs="?", help="refdes конденсатора, рядом с которым ставим виа")
    ap.add_argument("--offset-mm", type=float, default=1.2, help="смещение виа от конденсатора, мм")
    ap.add_argument("--net", default="GND")
    ap.add_argument("--drill-mm", type=float, default=0.3)
    ap.add_argument("--diameter-mm", type=float, default=0.6)
    ap.add_argument("--timeout-ms", type=int, default=30000)
    ap.add_argument("--remove", nargs="?", const="__last__", default=None, metavar="UUID",
                     help="удалить виа вместо создания новой. Без значения — берёт id последней "
                          "созданной этим скриптом виа из .last_test_via.json")
    args = ap.parse_args()

    adapter = step("KiCadBoardAdapter(...)", KiCadBoardAdapter, timeout_ms=args.timeout_ms)
    step("adapter.refresh_board()", adapter.refresh_board)

    if args.remove:
        remove_id = args.remove
        if remove_id == "__last__":
            if not STATE_FILE.exists():
                sys.exit(f"[ошибка] нет сохранённого id в {STATE_FILE} — передайте --remove <uuid> явно")
            remove_id = json.loads(STATE_FILE.read_text(encoding="utf-8"))["id"]
            print(f"Беру id из {STATE_FILE.name}: {remove_id}\n")

        # Используем адаптер для удаления по UUID
        commit = step("adapter.begin_commit()", adapter.begin_commit)
        try:
            step("adapter.remove_by_id(remove_id)", adapter.remove_by_id, remove_id)
            step("adapter.push_commit(commit, ...)", adapter.push_commit, commit, "test_create_one_via: remove")
            print(f"\nВиа {remove_id} удалена.")
            if STATE_FILE.exists() and remove_id == json.loads(STATE_FILE.read_text(encoding="utf-8"))["id"]:
                STATE_FILE.unlink()
        except Exception:
            step("adapter.drop_commit(commit)", adapter.drop_commit, commit)
            raise
        return

    if not args.ref:
        sys.exit("укажите refdes конденсатора (или --remove <uuid> для удаления)")

    fp = step(f"adapter.get_footprint({args.ref!r})", adapter.get_footprint, args.ref)
    if fp is None:
        sys.exit(f"[ошибка] {args.ref} не найден на плате")

    net = step(f"adapter.get_net_by_name({args.net!r})", adapter.get_net_by_name, args.net)
    if net is None:
        sys.exit(f"[ошибка] цепь {args.net!r} не найдена на плате")

    pos = fp.position
    via_pos = Vector2.from_xy(int(pos.x + args.offset_mm * MM), int(pos.y))
    print(f"\n{args.ref} на ({pos.x/MM:.3f}, {pos.y/MM:.3f}) мм, "
          f"виа будет на ({via_pos.x/MM:.3f}, {via_pos.y/MM:.3f}) мм, net={args.net}\n")

    via = adapter.create_via(via_pos, net, args.drill_mm, args.diameter_mm)

    commit = step("adapter.begin_commit()", adapter.begin_commit)
    try:
        created = step("adapter.create_items([via])", adapter.create_items, [via])
        step("adapter.push_commit(commit, ...)", adapter.push_commit, commit,
             f"test_create_one_via: рядом с {args.ref}")
        created_id = created[0].id.value if created else None
        print(f"\nГотово. Виа создана, id={created_id}")
        if created_id:
            STATE_FILE.write_text(json.dumps({"id": created_id, "ref": args.ref}), encoding="utf-8")
            print(f"id сохранён в {STATE_FILE.name} — чтобы удалить, просто:\n"
                  f"  python -m kicadspoke.diagnostics.test_create_one_via --remove")
    except Exception:
        step("adapter.drop_commit(commit)", adapter.drop_commit, commit)
        raise


if __name__ == "__main__":
    main()