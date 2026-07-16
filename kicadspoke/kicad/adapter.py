# kicadspoke/kicad/adapter.py

import time
import logging
from typing import List, Optional, Any
import kipy
from kipy.board_types import FootprintInstance, Zone, Net, Via, ViaType, BoardLayer, Pad, Field
from kipy.geometry import Vector2, Box2, Angle

from .interfaces import IBoardAdapter
from ..exceptions import BoardNotFoundError, ComponentNotFoundError
from ..utils.units import MM

logger = logging.getLogger(__name__)

class KiCadBoardAdapter(IBoardAdapter):
    def __init__(self, timeout_ms: int = 20000):
        logger.debug(f"Инициализация KiCadBoardAdapter с таймаутом {timeout_ms} мс")
        logger.debug("Создание экземпляра kipy.KiCad...")
        self._kicad = kipy.KiCad(timeout_ms=timeout_ms)
        logger.debug("Экземпляр kipy.KiCad создан")
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

    def get_vias(self) -> List[Via]:
        """Все существующие на плате виа (для проверки идемпотентности повторного прогона)."""
        vias = list(self._board.get_vias())
        logger.debug(f"Получено {len(vias)} виа")
        return vias

    def get_field_value(self, footprint: FootprintInstance, field_name: str) -> Optional[str]:
        """
        Значение пользовательского поля компонента (например, роль для
        KiCadSpoke 4.0). ВАЖНО: texts_and_fields содержит вперемешку
        настоящие Field (name+text.value) и голый BoardText (просто текст
        на шёлкографии, без имени поля вовсе) — фильтруем по типу, иначе
        типична ошибка AttributeError на .name у BoardText.
        """
        for item in footprint.texts_and_fields:
            if isinstance(item, Field) and item.name == field_name:
                return item.text.value if item.text else None
        return None

    def get_footprint_pads(self, footprint: FootprintInstance) -> List[Pad]:
        """
        Возвращает список пад данного футпринта. Не ходит в API отдельно —
        площадки уже лежат в footprint.definition.items вместе с полями/
        графикой, просто фильтруем по типу. Вынесено сюда из planner.py,
        чтобы не дублировать в будущих местах (например, при сборке
        keepout для виа — см. geometry/keepout.py).
        """
        return [item for item in footprint.definition.items if isinstance(item, Pad)]

    def get_pad_by_number(self, footprint: FootprintInstance, pad_number: str) -> Optional[Pad]:
        """Находит конкретную площадку футпринта по номеру (например, '1', '145')."""
        for pad in self.get_footprint_pads(footprint):
            if pad.number == pad_number:
                return pad
        return None

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

    # --- Bounding box (для коллизий — см. collision.py) ---
    def get_bounding_boxes(self, items) -> List[Optional[Box2]]:
        """
        Возвращает bounding box'ы (Box2 | None) для списка элементов ОДНИМ
        запросом. Board.get_item_bounding_box(list) возвращает List[Optional[Box2]]
        для последовательности элементов (для одного элемента вернул бы
        просто Box2|None — поэтому здесь всегда передаём список).
        """
        if not items:
            return []
        result = self._board.get_item_bounding_box(list(items))
        # На случай, если бы вдруг вернулся не список (защитная нормализация)
        if not isinstance(result, list):
            result = [result]
        return result

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
        """
        ИСПРАВЛЕНО (2026-07-12): раньше `commit = self.begin_commit()` был
        внутри try, но если begin_commit() САМ падал (реальный, воспроизведённый
        сценарий — см. историю с зависшей IPC-сессией и "KiCad is busy"),
        `commit` оставался НЕ ОПРЕДЕЛЁН, и `except: self.drop_commit(commit)`
        падал с UnboundLocalError, полностью маскируя настоящую причину.
        Теперь commit=None до try, drop_commit вызывается только если commit
        реально был получен.
        """
        last_exc = None
        for attempt in range(retries + 1):
            commit = None
            try:
                logger.debug(f"Попытка {attempt+1}/{retries+1} для {description}")
                commit = self.begin_commit()
                work_fn()
                self.push_commit(commit, description)
                return True
            except Exception as e:
                last_exc = e
                if commit is not None:
                    try:
                        self.drop_commit(commit)
                    except Exception as drop_exc:
                        logger.error(f"Не удалось откатить транзакцию {description}: {drop_exc}")
                logger.warning(f"Ошибка в транзакции {description} (попытка {attempt+1}): "
                               f"{type(e).__name__}: {e}")
                if attempt == retries:
                    raise
                time.sleep(0.5)
        # Сюда не дойдём (либо return True, либо raise выше), но на всякий случай:
        if last_exc:
            raise last_exc
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

