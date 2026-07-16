#!/usr/bin/env python3
"""
test_custom_field.py — проверяет, читается ли через IPC произвольное
пользовательское поле компонента (не Reference/Value/Footprint/Datasheet,
а то, что схемотехник сам добавил в Eeschema).

Это КЛЮЧЕВАЯ, пока не подтверждённая эмпирически зависимость и для
DecapPlacer 4.0 (роли вместо component1/component2), и для будущего
extract_template.py — если это не читается, обе идеи нужно пересматривать
до того, как в них вложен код.

Подготовка перед запуском:
  1. В Eeschema откройте нужный символ, добавьте новое поле (например,
     имя поля "TemplateRole", значение "HEAVY" или как договорились).
  2. Update PCB from Schematic — чтобы поле (если оно вообще
     переносится) попало на сторону PCB, где работает IPC.
  3. Запустите этот скрипт.

Запуск:
    python test_custom_field.py C5 --field TemplateRole
"""
import argparse
import sys
import time

import kipy
from kipy.board_types import Field, BoardText


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


def find_fp(board, ref):
    return next((fp for fp in board.get_footprints() if fp.reference_field.text.value == ref), None)


def describe_item(item):
    """
    texts_and_fields — список ВПЕРЕМЕШКУ Field (настоящие поля: name+text.value)
    и голого BoardText (просто текст на шёлкографии, без имени поля вовсе —
    например, кто-то вручную дорисовал надпись на футпринте).
    """
    if isinstance(item, Field):
        return item.name or "(поле без имени)", item.text.value if item.text else ""
    if isinstance(item, BoardText):
        return "(просто текст, не поле)", item.value
    return f"(неизвестный тип {type(item).__name__})", str(item)


def get_field_value(fp, field_name):
    """Ищет ИМЕННО Field (не BoardText) с нужным именем."""
    for item in fp.texts_and_fields:
        if isinstance(item, Field) and item.name == field_name:
            return item.text.value if item.text else None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="refdes компонента для проверки (например, C5)")
    ap.add_argument("--field", default="TemplateRole", help="имя пользовательского поля для поиска")
    ap.add_argument("--timeout-ms", type=int, default=20000)
    args = ap.parse_args()

    kicad = step("kipy.KiCad(...)", kipy.KiCad, timeout_ms=args.timeout_ms)
    board = step("kicad.get_board()", kicad.get_board)

    fp = find_fp(board, args.ref)
    if fp is None:
        sys.exit(f"[ошибка] {args.ref} не найден на плате")

    print(f"\n=== Все поля/тексты компонента {args.ref} ===")
    if not fp.texts_and_fields:
        print("  (список texts_and_fields пуст)")
    for item in fp.texts_and_fields:
        name, value = describe_item(item)
        print(f"  {name!r:<25} = {value!r}")

    print(f"\n=== Ищу поле {args.field!r} (именно Field, не голый текст) ===")
    value = get_field_value(fp, args.field)
    if value is None:
        print(f"[НЕ НАЙДЕНО] Поле {args.field!r} отсутствует в texts_and_fields этого компонента.")
        print("Возможные причины:")
        print("  1. Поле не добавлено в Eeschema для этого символа")
        print("  2. Update PCB from Schematic не выполнялся после добавления поля")
        print("  3. Поле не переносится с схемы на PCB вообще (тогда идею с ролями "
              "нужно пересматривать — читать поле пришлось бы через .net-файл, не через IPC)")
    else:
        print(f"[НАЙДЕНО] {args.field!r} = {value!r}")
        print(">>> Поле читается через IPC. Идея с ролями подтверждена эмпирически.")


if __name__ == "__main__":
    main()