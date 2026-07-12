# decap_placer/kicad/adapter.py

import time
import logging
from typing import List, Optional, Any
import kipy
from kipy.board_types import FootprintInstance, Zone, Net, Via, ViaType, BoardLayer
from kipy.geometry import Vector2, Angle
from kipy.proto.common.types import base_types_pb2 as common_types_pb2

from ..exceptions import BoardNotFoundError, ComponentNotFoundError
from ..utils.units import MM

logger = logging.getLogger(__name__)

class KiCadBoardAdapter:
    def __init__(self, timeout_ms: int = 20000):
        logger.debug(f"Инициализация KiCadBoardAdapter с таймаутом {timeout_ms} мс")
        self._kicad = kipy.KiCad(timeout_ms=timeout_ms)
        self._board = None

    def refresh_board(self):
        logger.debug("Обновление доски из KiCad")
        self._board = self._kicad.get_board()
        if self._board is None:
            raise BoardNotFoundError("Не удалось получить плату из KiCad")
        logger.info("Доска получена")

    # --- Поиск ---
    def get_footprint(self, ref: str) -> Optional[FootprintInstance]:
        for fp in self._board.get_footprints():
            if fp.reference_field.text.value == ref:
                logger.debug(f"Найден футпринт {ref}")
                return fp
        logger.debug(f"Футпринт {ref} не найден")
        return None

    def get_footprints(self) -> List[FootprintInstance]:
        fps = list(self._board.get_footprints())
        logger.debug(f"Получено {len(fps)} футпринтов")
        return fps

    def get_zone_by_name(self, name: str) -> Optional[Zone]:
        for z in self._board.get_zones():
            if z.name == name:
                logger.debug(f"Найдена зона {name}")
                return z
        logger.debug(f"Зона {name} не найдена")
        return None

    def get_net_by_name(self, name: str) -> Optional[Net]:
        for n in self._board.get_nets():
            if n.name == name:
                logger.debug(f"Найдена цепь {name}")
                return n
        logger.debug(f"Цепь {name} не найдена")
        return None

    def get_all_nets(self) -> List[Net]:
        nets = list(self._board.get_nets())
        logger.debug(f"Получено {len(nets)} цепей")
        return nets

    # --- Транзакции ---
    def begin_commit(self):
        logger.debug("Начало транзакции")
        return self._board.begin_commit()

    def push_commit(self, commit, description: str):
        logger.debug(f"Применение транзакции: {description}")
        self._board.push_commit(commit, description)
        logger.info(f"Транзакция применена: {description}")

    def drop_commit(self, commit):
        logger.warning("Откат транзакции")
        self._board.drop_commit(commit)

    def update_items(self, items):
        logger.debug(f"Обновление {len(items)} элементов")
        self._board.update_items(items)

    def create_items(self, items):
        logger.debug(f"Создание {len(items)} элементов")
        created = self._board.create_items(items)
        logger.debug(f"Создано {len(created)} элементов")
        return created

    # --- Специализированные действия ---
    def flip_selected(self, footprints: List[FootprintInstance]):
        logger.info(f"Флип {len(footprints)} футпринтов через GUI action")
        self._board.clear_selection()
        self._board.add_to_selection(footprints)
        self._kicad.run_action("pcbnew.InteractiveEdit.flip")
        self._board.clear_selection()
        logger.debug("Флип выполнен")

    def commit_with_retry(self, description: str, work_fn, retries: int = 1) -> bool:
        for attempt in range(retries + 1):
            try:
                logger.debug(f"Попытка {attempt+1}/{retries+1} для {description}")
                commit = self.begin_commit()
                work_fn()
                self.push_commit(commit, description)
                return True
            except Exception as e:
                self.drop_commit(commit)
                logger.warning(f"Ошибка в транзакции {description} (попытка {attempt+1}): {e}")
                if attempt == retries:
                    raise
                time.sleep(0.5)
        return False

    def create_via(self, position: Vector2, net: Net, drill_mm: float, diameter_mm: float) -> Via:
        logger.debug(f"Создание виа в ({position.x/MM:.3f}, {position.y/MM:.3f}) мм, net={net.name}")
        via = Via()
        via.type = ViaType.VT_THROUGH
        via.position = position
        via.net = net
        via.drill_diameter = int(drill_mm * MM)
        via.diameter = int(diameter_mm * MM)
        return via