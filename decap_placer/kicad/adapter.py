# decap_placer/kicad/adapter.py

import time
from typing import List, Optional, Any
import kipy
from kipy.board_types import FootprintInstance, Zone, Net, Via, ViaType, BoardLayer
from kipy.geometry import Vector2, Angle
from kipy.proto.common.types import base_types_pb2 as common_types_pb2

from ..exceptions import BoardNotFoundError, ComponentNotFoundError
from ..utils.units import MM

class KiCadBoardAdapter:
    def __init__(self, timeout_ms: int = 20000):
        self._kicad = kipy.KiCad(timeout_ms=timeout_ms)
        self._board = None

    def refresh_board(self):
        self._board = self._kicad.get_board()
        if self._board is None:
            raise BoardNotFoundError("Не удалось получить плату из KiCad")

    # --- Поиск ---
    def get_footprint(self, ref: str) -> Optional[FootprintInstance]:
        for fp in self._board.get_footprints():
            if fp.reference_field.text.value == ref:
                return fp
        return None

    def get_footprints(self) -> List[FootprintInstance]:
        return list(self._board.get_footprints())

    def get_zone_by_name(self, name: str) -> Optional[Zone]:
        for z in self._board.get_zones():
            if z.name == name:
                return z
        return None

    def get_net_by_name(self, name: str) -> Optional[Net]:
        for n in self._board.get_nets():
            if n.name == name:
                return n
        return None

    def get_all_nets(self) -> List[Net]:
        return list(self._board.get_nets())

    # --- Транзакции ---
    def begin_commit(self):
        return self._board.begin_commit()

    def push_commit(self, commit, description: str):
        self._board.push_commit(commit, description)

    def drop_commit(self, commit):
        self._board.drop_commit(commit)

    def update_items(self, items):
        self._board.update_items(items)

    def create_items(self, items):
        return self._board.create_items(items)

    # --- Специализированные действия ---
    def flip_selected(self, footprints: List[FootprintInstance]):
        """Флип через GUI action. После вызова нужно обновить ссылки на объекты."""
        self._board.clear_selection()
        self._board.add_to_selection(footprints)
        self._kicad.run_action("pcbnew.InteractiveEdit.flip")
        self._board.clear_selection()

    def commit_with_retry(self, description: str, work_fn, retries: int = 1) -> bool:
        """Выполняет work_fn внутри транзакции, при ошибке откатывает и повторяет."""
        for attempt in range(retries + 1):
            commit = self.begin_commit()
            try:
                work_fn()
                self.push_commit(commit, description)
                return True
            except Exception as e:
                self.drop_commit(commit)
                if attempt == retries:
                    raise
                time.sleep(0.5)  # небольшая пауза перед повторной попыткой
        return False

    # --- Создание виа ---
    def create_via(self, position: Vector2, net: Net, drill_mm: float, diameter_mm: float) -> Via:
        via = Via()
        via.type = ViaType.VT_THROUGH
        via.position = position
        via.net = net
        via.drill_diameter = int(drill_mm * MM)
        via.diameter = int(diameter_mm * MM)
        return via