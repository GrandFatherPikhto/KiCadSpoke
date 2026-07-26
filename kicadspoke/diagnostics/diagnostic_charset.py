#!/usr/bin/env python3
"""
diagnostics/diagnostic_charset.py — ищет по всей плате поля Role/Cluster
(или любые другие через --fields), содержащие символы не из печатной
ASCII (0x20-0x7E).

Повод: живая находка на 3CH-AWG-TIA — Role у трёх компонентов (C3, C9,
C170) содержал кириллическую "С" (U+0421) вместо латинской "C" (U+0043)
на месте первой буквы (C_IN_BYPASS -> С_IN_BYPASS и т.п.) — очевидно,
раскладка соскочила на русскую в момент набора значения поля в Eeschema.
Визуально неотличимо почти в любом шрифте, но ломает точное сравнение
ролей (component_pool.py/clone_role_resolver.py сравнивают Role строгим
равенством) — компонент с такой опечаткой не находится ни одним правилом,
которое ищет "правильную" (латинскую) роль, и наоборот, если её
переименовать в шаблоне на кириллицу — воспроизвести опечатку руками
почти невозможно, находится только диффом кодов символов.

Запуск:
    python -m kicadspoke.diagnostics.diagnostic_charset
    python -m kicadspoke.diagnostics.diagnostic_charset --fields Role,Cluster,Value
    python -m kicadspoke.diagnostics.diagnostic_charset --verbose

Код возврата: 0 — не найдено ни одного нелатинского символа, 1 — найдено
хотя бы одно. Удобно как самостоятельный шаг перед `apply` (аналогично
`run_all_checks`, но для этого класса опечаток нет отдельной проверки в
validation.py — она тут, в диагностике, а не в основном пайплайне, чтобы
не тормозить обычный apply лишним полным проходом по плате ради редкой
находки).
"""
import argparse
import logging
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadspoke.kicad.adapter import KiCadBoardAdapter

logger = logging.getLogger(__name__)

DEFAULT_FIELDS = ["Role", "Cluster"]


def find_non_ascii(value: str):
    """[(индекс, символ, кодпоинт, unicode-имя), ...] для каждого символа
    вне печатной ASCII (0x20-0x7E) — намеренно узкий диапазон, не просто
    "не ASCII": табы/переводы строк в однострочных полях Role/Cluster и
    так недопустимы, но здесь речь именно про подмену буквы похожим
    символом из другого алфавита, а не про whitespace-мусор."""
    bad = []
    for i, ch in enumerate(value):
        if not (0x20 <= ord(ch) <= 0x7E):
            try:
                name = unicodedata.name(ch)
            except ValueError:
                name = "БЕЗ ИМЕНИ"
            bad.append((i, ch, ord(ch), name))
    return bad


def main():
    ap = argparse.ArgumentParser(
        description="Поиск не-ASCII символов (например, кириллических "
                     "гомоглифов) в полях Role/Cluster по всей плате")
    ap.add_argument("--fields", default=",".join(DEFAULT_FIELDS),
                     help=f"через запятую, без пробелов (по умолчанию: {','.join(DEFAULT_FIELDS)})")
    ap.add_argument("--timeout-ms", type=int, default=20000)
    ap.add_argument("--verbose", action="store_true", help="печатать и чистые поля тоже")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]

    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()
    footprints = adapter.get_footprints()

    findings = []
    for fp in footprints:
        ref = fp.reference_field.text.value
        for field in fields:
            value = adapter.get_field_value(fp, field)
            if not value:
                continue
            bad = find_non_ascii(value)
            if bad:
                findings.append((ref, field, value, bad))
            elif args.verbose:
                logger.debug(f"{ref}.{field} = {value!r} — чисто")

    print(f"\nПроверено футпринтов: {len(footprints)}, полей на компонент: {fields}")

    if not findings:
        print("Не найдено ни одного не-ASCII символа. Всё чисто.")
        return 0

    print(f"\n=== НАЙДЕНО {len(findings)} поле(й) с подозрительными символами ===\n")
    for ref, field, value, bad in findings:
        print(f"{ref}.{field} = {value!r}")
        for i, ch, cp, name in bad:
            print(f"    позиция {i}: {ch!r} U+{cp:04X} ({name})")
    print(
        "\nЭто не обязательно ошибка (могут быть легитимные Unicode-значения "
        "в других полях), но для Role/Cluster ожидается чистая ASCII-латиница "
        "— сравнение ролей в component_pool.py/clone_role_resolver.py "
        "регистрозависимое и посимвольное, гомоглиф из другого алфавита "
        "не совпадёт ни с чем. Правьте в Eeschema (Symbol Properties -> "
        "стереть и перепечатать значение при английской раскладке), затем "
        "Update PCB from Schematic."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
