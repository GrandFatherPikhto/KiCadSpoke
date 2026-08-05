#!/usr/bin/env python3
"""
probe_kicad_sch_uuids.py — two-step check of the UUID-bridge idea.

Input:
    Path to the project directory containing *.kicad_sch files.

Expected:
    Step 1 (no live KiCad): parses every *.kicad_sch with sexpdata and builds
    {uuid: Sheetname} from the (sheet ...) blocks. Step 2 (live KiCad): reads
    each footprint's sheet_path.path UUIDs via kipy and reports how many are
    found in that map — a large overlap means kipy UUIDs really match the
    schematic UUIDs; a zero overlap closes the bridge idea for good.

Live KiCad:
    Partially — step 1 reads local files only; step 2 needs a running KiCad
    with the board open.

Run:
    python -m kicadstamp.diagnostics.probe_kicad_sch_uuids <project_dir>
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


def _sheet_uuid_and_name(sheet_node):
    uuid_nodes = _children(sheet_node, 'uuid')
    uuid_val = str(uuid_nodes[0][1]) if uuid_nodes else None
    name = None
    for prop in _children(sheet_node, 'property'):
        if len(prop) > 1 and str(prop[1]) == 'Sheetname':
            name = str(prop[2])
    return uuid_val, name


def scan_kicad_sch_dir(project_dir: str) -> dict:
    """{uuid: (Sheetname, файл-источник)} по ВСЕМ *.kicad_sch в директории."""
    result = {}
    for path in glob.glob(os.path.join(project_dir, "*.kicad_sch")):
        try:
            data = sexpdata.load(open(path, encoding='utf-8'))
        except Exception as e:
            print(f"  не удалось распарсить {path}: {e}")
            continue
        for sheet in _children(data, 'sheet'):
            uuid_val, name = _sheet_uuid_and_name(sheet)
            if uuid_val:
                result[uuid_val] = (name, os.path.basename(path))
    return result


def main():
    if len(sys.argv) < 2:
        print("Использование: python probe_kicad_sch_uuids.py <путь_к_папке_проекта>")
        sys.exit(1)
    project_dir = sys.argv[1]

    print(f"--- Шаг 1: сканирую *.kicad_sch в {project_dir} ---\n")
    uuid_to_name = scan_kicad_sch_dir(project_dir)
    print(f"Найдено (sheet ...) блоков с uuid: {len(uuid_to_name)}\n")
    for uuid_val, (name, src) in sorted(uuid_to_name.items(), key=lambda kv: kv[1][0] or ''):
        print(f"  {uuid_val}  ->  {name!r}  (в файле {src})")

    print(f"\n--- Шаг 2: сверяю с sheet_path.path через kipy ---\n")
    try:
        import kipy
    except ImportError:
        print("kipy не установлен в этом окружении — шаг 2 пропущен.")
        return

    try:
        kc = kipy.KiCad()
        board = kc.get_board()
    except Exception as e:
        print(f"Не удалось подключиться к KiCad ({e}) — шаг 2 пропущен, "
              f"запусти при открытом KiCad с нужной платой.")
        return

    footprints = list(board.get_footprints())
    total_uuids_seen = set()
    matched_uuids = set()

    for fp in footprints:
        ref = fp.reference_field.text.value
        path_uuids = [str(u.value) for u in fp.sheet_path.path]
        for u in path_uuids:
            total_uuids_seen.add(u)
            if u in uuid_to_name:
                matched_uuids.add(u)

    print(f"Всего уникальных UUID во всех sheet_path.path: {len(total_uuids_seen)}")
    print(f"Из них совпало со словарём из .kicad_sch: {len(matched_uuids)}")
    if matched_uuids:
        print("\nСовпавшие (первые 10):")
        for u in list(matched_uuids)[:10]:
            print(f"  {u} -> {uuid_to_name[u]}")
    else:
        print("\nСовпадений НЕТ — UUID из kipy sheet_path.path это не те же UUID, "
              "что в (sheet ...) блоках .kicad_sch. Мост закрыт окончательно.")


if __name__ == "__main__":
    main()