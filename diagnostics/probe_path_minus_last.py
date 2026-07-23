#!/usr/bin/env python3
"""
probe_path_minus_last.py — чистая финальная проверка: path[:-1]
(sheet_path.path без последнего элемента — предположительно, своего
UUID символа) сгруппированный через РЕАЛЬНЫЙ словарь {uuid: Sheetname}
из .kicad_sch (а не через шумные имена локальных цепей, как раньше).

Запуск: python probe_path_minus_last.py <путь_к_папке_проекта>
(KiCad с этой платой должен быть открыт)
"""
import sys
import io
import glob
import os
import sexpdata

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


def _children(node, tag):
    if not isinstance(node, list):
        return []
    return [n for n in node[1:] if isinstance(n, list) and n and str(n[0]) == tag]


def scan_kicad_sch_dir(project_dir: str) -> dict:
    result = {}
    for path in glob.glob(os.path.join(project_dir, "*.kicad_sch")):
        try:
            data = sexpdata.load(open(path, encoding='utf-8'))
        except Exception:
            continue
        for sheet in _children(data, 'sheet'):
            uuid_nodes = _children(sheet, 'uuid')
            uuid_val = str(uuid_nodes[0][1]) if uuid_nodes else None
            name = None
            for prop in _children(sheet, 'property'):
                if len(prop) > 1 and str(prop[1]) == 'Sheetname':
                    name = str(prop[2])
            if uuid_val:
                result[uuid_val] = name
    return result


def main():
    if len(sys.argv) < 2:
        print("Использование: python probe_path_minus_last.py <путь_к_папке_проекта>")
        sys.exit(1)
    project_dir = sys.argv[1]
    uuid_to_name = scan_kicad_sch_dir(project_dir)
    print(f"Словарь uuid->Sheetname: {len(uuid_to_name)} записей\n")

    import kipy
    kc = kipy.KiCad()
    board = kc.get_board()
    footprints = list(board.get_footprints())

    # group_key (без последнего uuid) -> список (ref, человекочит.путь по словарю)
    groups = {}
    unresolved = []  # компоненты, у которых path[:-1] содержит uuid НЕ из словаря
    for fp in footprints:
        ref = fp.reference_field.text.value
        path_uuids = [str(u.value) for u in fp.sheet_path.path]
        chain = path_uuids[:-1]  # без последнего -- предположительно, своего uuid символа
        key = tuple(chain)
        names = [uuid_to_name.get(u) for u in chain]
        if any(n is None for n in names):
            unresolved.append((ref, chain, names))
        groups.setdefault(key, []).append((ref, tuple(names)))

    print(f"Всего футпринтов: {len(footprints)}")
    print(f"Уникальных групп (по path[:-1]): {len(groups)}\n")

    conflicts = 0
    for key, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        refs = [m[0] for m in members]
        human_variants = set(m[1] for m in members)
        status = "OK" if len(human_variants) == 1 else "!!! КОНФЛИКТ"
        if len(human_variants) > 1:
            conflicts += 1
        print(f"[{status}] группа из {len(refs)} компонентов, путь(и): {human_variants}")
        print(f"    refs: {', '.join(sorted(refs)[:8])}" + (" ..." if len(refs) > 8 else ""))

    print(f"\nИтого: групп с конфликтом человекочит. пути внутри одной и той же "
         f"группы по uuid: {conflicts} из {len(groups)}")
    print(f"Компонентов, где path[:-1] содержит НЕИЗВЕСТНЫЙ словарю uuid: {len(unresolved)}")
    if unresolved:
        print("Примеры:")
        for ref, chain, names in unresolved[:5]:
            print(f"  {ref}: chain={chain}, names={names}")


if __name__ == "__main__":
    main()