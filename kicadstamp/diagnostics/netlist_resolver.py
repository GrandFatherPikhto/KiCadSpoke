#!/usr/bin/env python3
"""
netlist_resolver.py — deep dump of fp.sheet_path attributes via kipy.

Input:
    None (reads the live board).

Expected:
    For the first 20 footprints: prints every non-underscore attribute of
    sheet_path (path, proto, path_human_readable, uuid, ...) to see whether
    enough hierarchy information survives to rebuild the sheet tree.

Live KiCad:
    Yes — requires a running KiCad with the target board open.

Run:
    python -m kicadstamp.diagnostics.netlist_resolver
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
print("Для получения имён листов используйте .net файл и kicadstamp.cloner.netlist.")
print("Пример: python -c \"from kicadstamp.cloner.netlist import parse_netlist; comps,_,_ = parse_netlist('project.net'); [print(c.ref, c.sheet_names) for c in comps]\"")