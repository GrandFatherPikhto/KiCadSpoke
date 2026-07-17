#!/usr/bin/env python3
"""
diagnostics/test_custom_field.py — проверяет, читается ли через IPC произвольное
пользовательское поле компонента (например, Role) с использованием адаптера KiCadSpoke.

Запуск:
    python -m kicadspoke.diagnostics.test_custom_field C5 --field Role
"""

import argparse
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadspoke.kicad.adapter import KiCadBoardAdapter

logger = logging.getLogger(__name__)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="refdes компонента для проверки (например, C5)")
    ap.add_argument("--field", default="Role", help="имя пользовательского поля для поиска")
    ap.add_argument("--timeout-ms", type=int, default=20000)
    ap.add_argument("--verbose", action="store_true", help="подробный вывод")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()

    fp = adapter.get_footprint(args.ref)
    if fp is None:
        sys.exit(f"[ошибка] {args.ref} не найден на плате")

    print(f"\n=== Все поля/тексты компонента {args.ref} ===")
    texts_and_fields = fp.texts_and_fields
    if not texts_and_fields:
        print("  (список texts_and_fields пуст)")
    for item in texts_and_fields:
        from kipy.board_types import Field, BoardText
        if isinstance(item, Field):
            name = item.name or "(поле без имени)"
            value = item.text.value if item.text else ""
        elif isinstance(item, BoardText):
            name = "(просто текст, не поле)"
            value = item.value
        else:
            name = f"(неизвестный тип {type(item).__name__})"
            value = str(item)
        print(f"  {name!r:<25} = {value!r}")

    print(f"\n=== Ищу поле {args.field!r} (именно Field, не голый текст) ===")
    value = adapter.get_field_value(fp, args.field)
    if value is None:
        print(f"[НЕ НАЙДЕНО] Поле {args.field!r} отсутствует.")
        print("Возможные причины:")
        print("  1. Поле не добавлено в Eeschema для этого символа")
        print("  2. Update PCB from Schematic не выполнялся после добавления поля")
        print("  3. Поле не переносится с схемы на PCB (тогда читать его нужно через .net-файл)")
    else:
        print(f"[НАЙДЕНО] {args.field!r} = {value!r}")
        print(">>> Поле читается через IPC.")


if __name__ == "__main__":
    main()