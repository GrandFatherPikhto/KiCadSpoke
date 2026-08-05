"""
probe_footprints_fields.py — probes whether kipy can read/write custom fields
on a placed footprint (FootprintInstance), not just its library definition.

Input:
    None (connects to the live board and inspects the first footprint).

Expected:
    Prints which field APIs exist (get_fields/fields/get_field_by_name) and
    whether a custom field (KicadSpokeRole) can actually be read and written.

Live KiCad:
    Yes — requires a running KiCad with the target board open.
    CAUTION: this probe WRITES a test field (KicadSpokeRole=TEST_ROLE) to the
    first footprint on the board — it is not read-only.

Run:
    python -m kicadstamp.diagnostics.probe_footprints_fields
"""

from kipy import KiCad

def main():
    kicad = KiCad()
    board = kicad.get_board()
    footprints = board.get_footprints()
    if not footprints:
        print("На плате нет футпринтов")
        return

    fp = footprints[0]
    print(f"Пробуем на {fp.reference_field.text.value}")

    # 1. Есть ли метод для чтения всех полей?
    for attr in ("get_fields", "fields", "get_field_by_name"):
        has = hasattr(fp, attr)
        print(f"  hasattr(fp, '{attr}') = {has}")

    # 2. Пробуем реально прочитать поля
    try:
        fields = fp.get_fields() if hasattr(fp, "get_fields") else None
        print("  get_fields() ->", fields)
    except Exception as e:
        print("  get_fields() FAILED:", e)

    # 3. Пробуем создать/записать своё поле (главный вопрос)
    try:
        fp.set_field("KicadSpokeRole", "TEST_ROLE")  # имя метода - предположение,
        # реальное может отличаться; если это не сработает - смотрите вывод dir()
        board.update_items(fp)
        print("  set_field() -> похоже, сработало")
    except Exception as e:
        print("  set_field() FAILED:", e)

    print("\nПолный список доступных атрибутов/методов на FootprintInstance:")
    print([a for a in dir(fp) if "field" in a.lower()])

if __name__ == "__main__":
    main()