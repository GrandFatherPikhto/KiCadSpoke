"""
Проба: поддерживает ли kicad-python (kipy) чтение/запись пользовательских
полей у футпринта, РАЗМЕЩЁННОГО НА ПЛАТЕ (FootprintInstance), а не только
у определения в библиотеке.

Известная неопределённость на момент написания:
- футпринты в формате .kicad_pcb поддерживают именованные поля с версии KiCad 8
- но пользователь kicad-python сообщал (на версии 0.3.0), что API футпринтов
  на плате не поддерживает пользовательские поля вовсе
Проверяем честно, на вашей версии 0.7.1, а не гадаем.
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