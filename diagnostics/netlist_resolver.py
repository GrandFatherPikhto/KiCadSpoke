#!/usr/bin/env python3
"""
probe_sheet_path_deep.py — глубокое исследование fp.sheet_path через kipy.
Выводит все доступные атрибуты и пытается восстановить иерархию.
"""
import kipy
from kipy.board_types import FootprintInstance

kc = kipy.KiCad()
board = kc.get_board()
footprints = list(board.get_footprints())

print(f"Всего футпринтов: {len(footprints)}\n")

for fp in footprints[:20]:  # ограничим для наглядности
    ref = fp.reference_field.text.value
    sp = fp.sheet_path
    print(f"\n=== {ref} ===")
    print(f"  sp: {sp!r}")
    print(f"  dir(sp): {[attr for attr in dir(sp) if not attr.startswith('_')]}")
    # Попробуем получить path (список UUID)
    if hasattr(sp, 'path'):
        print(f"  sp.path: {sp.path!r}")
        if sp.path:
            print(f"    len(sp.path) = {len(sp.path)}")
            for i, item in enumerate(sp.path):
                print(f"      [{i}] {item!r}")
    # Попробуем proto
    if hasattr(sp, 'proto'):
        print(f"  sp.proto: {sp.proto!r}")
    # Попробуем path_human_readable (скорее всего пусто)
    if hasattr(sp, 'path_human_readable'):
        print(f"  sp.path_human_readable: {sp.path_human_readable!r}")
    # Попробуем другие возможные поля
    for attr in ['uuid', 'name', 'sheet_path', 'path_string']:
        if hasattr(sp, attr):
            print(f"  sp.{attr}: {getattr(sp, attr)!r}")

print("\nЕсли path_human_readable пуст, значит KiCad не сохраняет имена в PCB.")
print("Для получения имён листов используйте .net файл и kicadspoke.cloner.netlist.")
print("Пример: python -c \"from kicadspoke.cloner.netlist import parse_netlist; comps,_,_ = parse_netlist('project.net'); [print(c.ref, c.sheet_names) for c in comps]\"")