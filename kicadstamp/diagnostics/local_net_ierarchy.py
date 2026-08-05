#!/usr/bin/env python3
"""
local_net_ierarchy.py — dumps all local (hierarchical) net names on the board.

Input:
    None (reads the live board).

Expected:
    Prints every net whose name starts with '/' (KiCad's marker for a local
    hierarchical net) and the total count — to check whether local net names
    embed the full sheet path and can distinguish cloned-sheet instances
    (Channel_0/1/2) without the broken sheet_path.path_human_readable.

Live KiCad:
    Yes — requires a board with at least one cloned sheet for a meaningful run.

Run:
    python -m kicadstamp.diagnostics.local_net_ierarchy
"""
import kipy

kc = kipy.KiCad()
board = kc.get_board()

print("--- Все локальные (иерархические) цепи на плате ---\n")
local_nets = [n.name for n in board.get_nets() if n.name.startswith('/')]
for name in sorted(local_nets):
    print(f"  {name!r}")

print(f"\nВсего локальных цепей: {len(local_nets)}")