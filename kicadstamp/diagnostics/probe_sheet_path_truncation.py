#!/usr/bin/env python3
"""
probe_sheet_path_truncation.py — checks whether sheet_path.path starts or
ends with the symbol's own unique UUID.

Input:
    None (reads the live board; redirect stdout to a file for later diffing).

Expected:
    Groups components by path[:-1] (drop last) and path[1:] (drop first) and
    compares both groupings against the human-readable path derived from local
    nets — whichever variant keys all components of one sheet identically wins.

Live KiCad:
    Yes — requires a running KiCad with the board open.

Run:
    python -m kicadstamp.diagnostics.probe_sheet_path_truncation > sheet_truncation.txt
"""
import sys
import io
import kipy
from kipy.board_types import Pad

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

kc = kipy.KiCad()
board = kc.get_board()
footprints = list(board.get_footprints())


def raw_path(fp):
    """Список строк UUID как есть, без protobuf-обёртки."""
    return [str(u.value) for u in fp.sheet_path.path]


def get_pads(fp):
    try:
        return [p for p in fp.definition.items if isinstance(p, Pad)]
    except Exception:
        return []


def human_path_from_net(net_name: str):
    if not net_name.startswith('/'):
        return None
    parts = [p for p in net_name.split('/') if p]
    return tuple(parts[:-1])


# Собираем: refdes -> (полный path, человекочитаемый путь или None)
data = []
for fp in footprints:
    ref = fp.reference_field.text.value
    path = raw_path(fp)
    humans = set()
    for pad in get_pads(fp):
        if pad.net and pad.net.name.startswith('/'):
            humans.add(human_path_from_net(pad.net.name))
    data.append((ref, path, humans))

print(f"Всего футпринтов: {len(data)}\n")
print(f"Длины path: {sorted(set(len(p) for _, p, _ in data))}\n")

for variant_name, cut in [("path[:-1] (без последнего)", lambda p: tuple(p[:-1])),
                          ("path[1:] (без первого)", lambda p: tuple(p[1:])),
                          ("path целиком", lambda p: tuple(p))]:
    print(f"=== Вариант: {variant_name} ===")
    key_to_humans = {}
    key_to_count = {}
    for ref, path, humans in data:
        key = cut(path)
        key_to_count[key] = key_to_count.get(key, 0) + 1
        key_to_humans.setdefault(key, set()).update(humans)

    n_unique = len(key_to_count)
    n_multi = sum(1 for c in key_to_count.values() if c > 1)
    conflicts = sum(1 for humans in key_to_humans.values() if len(humans) > 1)
    print(f"  уникальных ключей: {n_unique} (из {len(data)} футпринтов), "
          f"ключей с 2+ компонентами: {n_multi}, ключей с противоречивыми "
          f"человекочит. путями: {conflicts}")
    print()