#!/usr/bin/env python3
"""
probe_uuid_to_sheet_name.py — строим словарь {sheet_path (UUID-цепочка,
как строка) -> человекочитаемый путь листа}, беря имя из ЛОКАЛЬНЫХ цепей
(начинаются с '/') компонентов этого же листа, и проверяем, действительно
ли UUID-цепочка стабильно совпадает у ВСЕХ компонентов одного и того же
(под)листа — независимо от того, сидят ли они сами на локальной цепи.

Идея: если это подтвердится, можно подписывать человекочитаемым именем
ЛЮБОЙ футпринт по его sheet_path, даже если сам он только на GND/+3V3 и
ни разу не касается локальной иерархической метки — имя берём у соседей.

Запуск: python probe_uuid_to_sheet_name.py
(KiCad должен быть открыт с платой, где есть клонированные листы)
"""
import sys
import io
import kipy
from kipy.board_types import Pad

# та самая грабля с прошлого раза: print() в перенаправленный (>) файл на
# Windows берёт кодировку консоли (обычно cp1251), а не UTF-8 — форсируем явно
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

kc = kipy.KiCad()
board = kc.get_board()
footprints = list(board.get_footprints())


def sheet_key(fp) -> str:
    """Тот же самый способ, что и _sheet_key() в clone_role_resolver.py —
    непрозрачная строка всей цепочки UUID, для сравнения "тот же лист"."""
    return str(fp.sheet_path.proto if hasattr(fp.sheet_path, 'proto') else fp.sheet_path)


def human_path_from_net(net_name: str):
    """'/Channel_0/DAC/DAC_FS_ADJ' -> ['Channel_0', 'DAC'] (без финальной метки)."""
    if not net_name.startswith('/'):
        return None
    parts = [p for p in net_name.split('/') if p]
    return parts[:-1]  # без самой метки цепи, только путь по листам


def get_pads(fp):
    try:
        return [p for p in fp.definition.items if isinstance(p, Pad)]
    except Exception:
        return []


# --- Шаг 1: для каждого футпринта с локальной цепью запоминаем sheet_key -> человекочитаемый путь ---
key_to_human = {}   # sheet_key -> список человекочитаемых путей, увиденных для этого ключа
key_to_refs = {}     # sheet_key -> список refdes, увиденных с этим ключом (для проверки совпадений)

for fp in footprints:
    ref = fp.reference_field.text.value
    key = sheet_key(fp)
    key_to_refs.setdefault(key, []).append(ref)

    for pad in get_pads(fp):
        if pad.net and pad.net.name.startswith('/'):
            human = human_path_from_net(pad.net.name)
            key_to_human.setdefault(key, set()).add(tuple(human))

# --- Шаг 2: печатаем — для каждого sheet_key: сколько refdes, какие человекочитаемые пути увидены ---
print(f"Всего футпринтов: {len(footprints)}, уникальных sheet_key: {len(key_to_refs)}\n")

for key, refs in sorted(key_to_refs.items(), key=lambda kv: -len(kv[1])):
    humans = key_to_human.get(key, set())
    humans_str = ", ".join("/".join(h) for h in humans) if humans else "(нет локальных цепей у этого листа)"
    print(f"sheet_key={key[:40]}...  refdes={len(refs)} шт  человекочитаемый путь: {humans_str}")
    print(f"    refs: {', '.join(sorted(refs))}")
    if len(humans) > 1:
        print(f"    !!! ВНИМАНИЕ: у одного sheet_key несколько РАЗНЫХ человекочитаемых путей — гипотеза не подтверждается тут")
    print()