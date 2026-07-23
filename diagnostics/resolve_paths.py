#!/usr/bin/env python3
"""
resolve_sheet_paths.py — восстанавливает человекочитаемые пути листов
для компонентов на плате, используя .net файл.
Запуск из корня проекта: python resolve_sheet_paths.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kicadspoke.cloner.netlist import parse_netlist
import kipy

def build_sheet_path_map(net_path):
    """Возвращает словарь: tuple_of_UUID -> human_readable_sheet_path"""
    comps, _, _ = parse_netlist(net_path)
    path_map = {}
    for c in comps:
        uuid_chain = tuple(c.sheet_tstamps.strip('/').split('/'))
        if uuid_chain not in path_map:
            path_map[uuid_chain] = c.sheet_names.strip('/')
    return path_map

def main():
    net_path = "project.net"
    if not Path(net_path).exists():
        print(f"Файл {net_path} не найден. Укажите правильный путь.")
        return

    path_map = build_sheet_path_map(net_path)

    kc = kipy.KiCad()
    board = kc.get_board()

    print("Refdes    Human-readable path")
    print("------------------------------")
    for fp in board.get_footprints():
        sp = fp.sheet_path
        uuid_chain = tuple(str(item.value) for item in sp.path)
        human_path = path_map.get(uuid_chain, "unknown")
        print(f"{fp.reference_field.text.value:10s}  {human_path}")

if __name__ == "__main__":
    main()