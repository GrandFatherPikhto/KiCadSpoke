#!/usr/bin/env python3
"""
probe_local_net_hierarchy.py — проверка идеи: содержат ли имена ЛОКАЛЬНЫХ
(иерархических) цепей полный путь по листам, и можно ли по этому пути
различить экземпляры клонированного листа (Channel_0/1/2 у mishin-coil)
без сломанного sheet_path.path_human_readable.

'/' в начале имени цепи — маркер локальной/иерархической метки в KiCad
(в отличие от глобальных GND/+5V без слэша). Смотрим, отличаются ли имена
одной и той же локальной метки в разных экземплярах листа.

Запуск: python probe_local_net_hierarchy.py
(KiCad должен быть открыт с платой, где реально есть клонированный лист)
"""
import kipy

kc = kipy.KiCad()
board = kc.get_board()

print("--- Все локальные (иерархические) цепи на плате ---\n")
local_nets = [n.name for n in board.get_nets() if n.name.startswith('/')]
for name in sorted(local_nets):
    print(f"  {name!r}")

print(f"\nВсего локальных цепей: {len(local_nets)}")