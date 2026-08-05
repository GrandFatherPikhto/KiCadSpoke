#!/usr/bin/env python3
"""
kipy_uuild_resolver.py — lists every net on the board with the refs connected.

Input:
    None (reads the live board).

Expected:
    For each net: name, code, count of connected components, and the refs list
    (deduplicated) — a quick net-connectivity overview via kipy.

Live KiCad:
    Yes — requires a running KiCad with the target board open.

Run:
    python -m kicadstamp.diagnostics.kipy_uuild_resolver
"""
import kipy
from collections import defaultdict

def main():
    kc = kipy.KiCad()
    board = kc.get_board()
    if board is None:
        print("Не удалось получить плату. Убедитесь, что KiCad открыт.")
        return

    # Получаем все цепи
    nets = list(board.get_nets())
    print(f"Всего цепей: {len(nets)}\n")

    # Строим словарь: цепь -> список refdes компонентов, подключённых к ней
    net_to_components = defaultdict(list)
    footprints = list(board.get_footprints())

    for fp in footprints:
        ref = fp.reference_field.text.value
        pads = [p for p in fp.definition.items if isinstance(p, kipy.board_types.Pad)]
        for pad in pads:
            if pad.net:
                net_to_components[pad.net.name].append(ref)

    # Выводим информацию по каждой цепи
    for net in nets:
        refs = net_to_components.get(net.name, [])
        print(f"[{net.name}] (code={net.code}) -> {len(refs)} компонентов: {', '.join(set(refs))}")

if __name__ == "__main__":
    main()