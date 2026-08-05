#!/usr/bin/env python3
"""
role_resolver.py — raw proto dump of each footprint's sheet_path.

Input:
    None (reads the live board).

Expected:
    For every footprint: refdes and the string form of sheet_path.proto — a
    quick one-shot look at what the proto representation actually contains.

Live KiCad:
    Yes — requires a running KiCad with the board open.

Run:
    python -m kicadstamp.diagnostics.role_resolver
"""
import kipy
from kipy.board_types import FootprintInstance

kc = kipy.KiCad()
board = kc.get_board()
footprints = board.get_footprints()

for fp in footprints:
    ref = fp.reference_field.text.value
    sp = fp.sheet_path
    # Попытка получить прото-представление
    try:
        proto_str = str(sp.proto)
    except AttributeError:
        proto_str = "(no proto)"
    print(f"{ref:10s}  proto={proto_str}")