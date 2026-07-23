#!/usr/bin/env python3
"""
probe_sheet_path.py — разовая проверка: что реально отдаёт
fp.sheet_path.path_human_readable на живой плате с клонированными
листами (Channel_0/Channel_1/Channel_2 у mishin-coil, например).

Запуск: python probe_sheet_path.py
(KiCad должен быть открыт с нужной платой)
"""
import kipy
from kipy.board_types import FootprintInstance

kc = kipy.KiCad()
board = kc.get_board()
footprints = board.get_footprints()

print(f"Всего футпринтов: {len(footprints)}\n")
for fp in footprints:
    ref = fp.reference_field.text.value
    sp = fp.sheet_path
    print(f"{ref:10s}  path_human_readable={sp.path_human_readable!r}  "
          f"path (UUID-чейн, длина)={len(sp.path)}")