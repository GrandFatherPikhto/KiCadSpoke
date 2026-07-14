#!/usr/bin/env python3
"""
test_create_one_via.py — минимальный диагностический тест create_items().

Цель: проверить именно СОЗДАНИЕ нового объекта (Via) через IPC — это
отдельный от update_items() код-путь, который в основном скрипте ещё ни
разу не отработал вживую (test_move_one_cap.py проверил только
update_items на существующем footprint).

Ставит одну виа на GND рядом с указанным конденсатором (со сдвигом
offset-mm от ЦЕНТРА конденсатора в сторону от платы — не от его площадки,
так что при небольшом offset для мелких футпринтов (0603 и т.п.) виа может
оказаться прямо под площадкой самого конденсатора — это ожидаемо для
этого теста, в боевом скрипте офсет считается иначе, от вывода FPGA).

Id созданной виа сохраняется в .last_test_via.json рядом со скриптом —
для удаления не нужно копировать uuid руками, --remove без аргумента сам
его подхватит.

Запуск:
    python test_create_one_via.py C5 --offset-mm 1.2
    python test_create_one_via.py --remove              # удалить последнюю созданную
    python test_create_one_via.py --remove <uuid>        # удалить конкретную, если нужно
"""
import argparse
import json
import sys
import time
from pathlib import Path

import kipy
from kipy.board_types import Via, ViaType
from kipy.geometry import Vector2
from kipy.proto.common.types import base_types_pb2 as common_types_pb2

MM = 1_000_000
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
                          "созданной этим скриптом виа из .last_test_via.json (не нужно копировать "
                          "uuid руками). Можно передать конкретный uuid явно, если нужно другое.")
    args = ap.parse_args()

    kicad = step("kipy.KiCad(...)", kipy.KiCad, timeout_ms=args.timeout_ms)
    board = step("kicad.get_board()", kicad.get_board)

    if args.remove:
        remove_id = args.remove
        if remove_id == "__last__":
            if not STATE_FILE.exists():
                sys.exit(f"[ошибка] нет сохранённого id в {STATE_FILE} — передайте --remove <uuid> явно")
            remove_id = json.loads(STATE_FILE.read_text(encoding="utf-8"))["id"]
            print(f"Беру id из {STATE_FILE.name}: {remove_id}\n")

        kiid = common_types_pb2.KIID()
        kiid.value = remove_id
        commit = step("board.begin_commit()", board.begin_commit)
        try:
            step("board.remove_items_by_id([...])", board.remove_items_by_id, [kiid])
            step("board.push_commit(commit, ...)", board.push_commit, commit, "test_create_one_via: remove")
            print(f"\nВиа {remove_id} удалена.")
            if STATE_FILE.exists() and remove_id == json.loads(STATE_FILE.read_text(encoding="utf-8"))["id"]:
                STATE_FILE.unlink()
        except Exception:
            step("board.drop_commit(commit)", board.drop_commit, commit)
            raise
        return

    if not args.ref:
        sys.exit("укажите refdes конденсатора (или --remove <uuid> для удаления)")

    footprints = step("board.get_footprints()", lambda: list(board.get_footprints()))
    target = next((fp for fp in footprints if fp.reference_field.text.value == args.ref), None)
    if target is None:
        sys.exit(f"[ошибка] {args.ref} не найден на плате")

    nets = step("board.get_nets()", lambda: list(board.get_nets()))
    net = next((n for n in nets if n.name == args.net), None)
    if net is None:
        sys.exit(f"[ошибка] цепь {args.net!r} не найдена на плате")

    pos = target.position
    via_pos = Vector2.from_xy(int(pos.x + args.offset_mm * MM), int(pos.y))
    print(f"\n{args.ref} на ({pos.x/MM:.3f}, {pos.y/MM:.3f}) мм, "
          f"виа будет на ({via_pos.x/MM:.3f}, {via_pos.y/MM:.3f}) мм, net={args.net}\n")

    via = Via()
    via.type = ViaType.VT_THROUGH
    via.position = via_pos
    via.net = net
    via.drill_diameter = int(args.drill_mm * MM)
    via.diameter = int(args.diameter_mm * MM)

    commit = step("board.begin_commit()", board.begin_commit)
    try:
        created = step("board.create_items([via])", board.create_items, [via])
        step("board.push_commit(commit, ...)", board.push_commit, commit, f"test_create_one_via: рядом с {args.ref}")
        created_id = created[0].id.value if created else None
        print(f"\nГотово. Виа создана, id={created_id}")
        if created_id:
            STATE_FILE.write_text(json.dumps({"id": created_id, "ref": args.ref}), encoding="utf-8")
            print(f"id сохранён в {STATE_FILE.name} — чтобы удалить, просто:\n"
                  f"  python test_create_one_via.py --remove")
    except Exception:
        step("board.drop_commit(commit)", board.drop_commit, commit)
        raise


if __name__ == "__main__":
    main()