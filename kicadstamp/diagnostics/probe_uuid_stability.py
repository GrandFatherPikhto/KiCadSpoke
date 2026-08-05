#!/usr/bin/env python3
"""
probe_uuid_stability.py — checks whether a footprint's own UUID (fp.id.value)
survives re-annotation (renaming) in the schematic.

Input:
    Output filename argument (e.g. uuid_before.txt / uuid_after.txt).

Expected:
    Writes ref -> fp.id.value for every board footprint to the given file.
    Protocol: run to uuid_before.txt, re-annotate in Eeschema (Update PCB from
    Schematic, Match Method = "Re-associate by UUID/timestamp", NOT by
    reference), run to uuid_after.txt, then diff the two files.

Live KiCad:
    Yes — requires a running KiCad with the board open.

Run:
    python -m kicadstamp.diagnostics.probe_uuid_stability <outfile.txt>
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
