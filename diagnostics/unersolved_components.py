#!/usr/bin/env python3
"""
resolve_channel_by_net.py — определяет канал (Channel_0/1/2) для каждого
компонента на основе анализа цепей, к которым подключены его пады.

Запуск: python resolve_channel_by_net.py
"""
import kipy
import re
from collections import defaultdict

CHANNEL_PATTERN = re.compile(r'^/(Channel_\d+)/')

def get_component_channel(fp):
    """Определяет канал компонента по цепям его падов."""
    channels = set()
    for pad in [p for p in fp.definition.items if isinstance(p, kipy.board_types.Pad)]:
        if pad.net and pad.net.name:
            match = CHANNEL_PATTERN.match(pad.net.name)
            if match:
                channels.add(match.group(1))
    if len(channels) > 1:
        # Если компонент подключён к нескольким каналам — это ошибка или особый случай
        return "MULTI: " + ", ".join(channels)
    elif len(channels) == 1:
        return next(iter(channels))
    else:
        return None  # компонент не подключён к иерархическим цепям

def main():
    kc = kipy.KiCad()
    board = kc.get_board()
    if board is None:
        print("Не удалось получить плату. Убедитесь, что KiCad открыт.")
        return

    footprints = list(board.get_footprints())
    print(f"Всего компонентов: {len(footprints)}\n")

    # Группируем компоненты по каналу
    by_channel = defaultdict(list)
    for fp in footprints:
        ref = fp.reference_field.text.value
        channel = get_component_channel(fp)
        if channel:
            by_channel[channel].append(ref)
        else:
            by_channel["(no channel)"].append(ref)

    for channel, refs in sorted(by_channel.items()):
        print(f"{channel}: {len(refs)} компонентов")
        # Можно вывести первые 10 для краткости
        print(f"  {', '.join(refs[:10])}{' ...' if len(refs) > 10 else ''}")
        print()

if __name__ == "__main__":
    main()