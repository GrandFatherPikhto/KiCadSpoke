#!/usr/bin/env python3
"""
probe_uuid_stability.py — проверка: переживает ли fp.id.value (собственный
UUID футпринта на плате) переименование (реаннотацию) компонента?

Протокол:
  1. Прогнать СЕЙЧАС (до переименования), сохранить вывод в файл (например, uuid_before.txt).
  2. В схеме переименовать один-два компонента (обычная реаннотация,
     через Update PCB from Schematic с Match Method = "Re-associate by
     UUID/timestamp" — НЕ "by reference").
  3. Прогнать ЕЩЁ РАЗ в другой файл (uuid_after.txt).
  4. Сравнить: у переименованного компонента (уже под новым ref) — тот
     же id.value, что был у него под старым ref, или другой?

Запуск: python probe_uuid_stability.py uuid_before.txt
        (переименовать, Update PCB from Schematic)
        python probe_uuid_stability.py uuid_after.txt
        diff uuid_before.txt uuid_after.txt
"""
import sys
import kipy

# Проверяем, передан ли аргумент с именем файла
if len(sys.argv) < 2:
    print(f"Ошибка: Не указано имя файла для вывода.", file=sys.stderr)
    print(f"Использование: python {sys.argv[0]} <имя_файла.txt>", file=sys.stderr)
    sys.exit(1)

output_filename = sys.argv[1]

kc = kipy.KiCad()
board = kc.get_board()
footprints = list(board.get_footprints())

# Открываем файл на запись с принудительным UTF-8
with open(output_filename, 'w', encoding='utf-8') as f:
    f.write(f"Всего футпринтов: {len(footprints)}\n\n")
    
    for fp in sorted(footprints, key=lambda f: f.reference_field.text.value):
        ref = fp.reference_field.text.value
        f.write(f"{ref:10s}  id={fp.id.value}\n")

print(f"Готово! Данные успешно сохранены в файл: {output_filename}")
