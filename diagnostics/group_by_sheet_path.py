#!/usr/bin/env python3
"""
group_by_sheet_path.py — группирует компоненты по UUID цепочкам sheet_path.
Показывает, какие компоненты принадлежат одному экземпляру листа.

Запуск: python group_by_sheet_path.py
"""
import sys
from collections import defaultdict
import kipy

def main():
    kc = kipy.KiCad()
    board = kc.get_board()
    if board is None:
        print("Не удалось получить плату. Убедитесь, что KiCad открыт.")
        sys.exit(1)

    footprints = list(board.get_footprints())
    print(f"Всего компонентов: {len(footprints)}\n")

    # Группируем по цепочке UUID
    groups = defaultdict(list)

    for fp in footprints:
        ref = fp.reference_field.text.value
        sp = fp.sheet_path
        # Преобразуем путь в кортеж строк UUID
        uuid_chain = tuple(str(item.value) for item in sp.path)
        groups[uuid_chain].append(ref)

    # Сортируем группы по количеству компонентов (по убыванию)
    sorted_groups = sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)

    print(f"Уникальных групп (листов): {len(sorted_groups)}\n")
    print("Группа (UUID цепочка) -> количество компонентов, примеры refdes\n")
    print("-" * 80)

    for i, (uuid_chain, refs) in enumerate(sorted_groups, 1):
        depth = len(uuid_chain)
        sample = ", ".join(refs[:5])
        if len(refs) > 5:
            sample += f" ... и ещё {len(refs) - 5}"
        print(f"{i:3d}. depth={depth}, count={len(refs):3d}  {sample}")
        # Если хотите увидеть полную цепочку UUID, раскомментируйте:
        # print(f"     UUIDs: {' -> '.join(uuid_chain)}")

    # Дополнительно: показать компоненты, у которых sheet_path пустой (обычно это компоненты без иерархии)
    empty_group = groups.get((), [])
    if empty_group:
        print(f"\nКомпоненты без sheet_path (глобальные, не в иерархии): {len(empty_group)}")
        print(f"  Примеры: {', '.join(empty_group[:10])}")

if __name__ == "__main__":
    main()