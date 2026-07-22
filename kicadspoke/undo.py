# kicadspoke/undo.py

import json
import logging
from pathlib import Path
from kipy.board_types import BoardLayer
from kipy.geometry import Vector2, Angle
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.utils.units import MM

logger = logging.getLogger(__name__)


def undo_last_operation(json_path: Path) -> bool:
    """Откатывает операцию, описанную в JSON-файле."""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()
    board = adapter._board
    if board is None:
        logger.error("Не удалось получить плату.")
        return False

    # 1. Восстановить перемещённые компоненты
    for item in data.get('moves', []):
        ref = item['ref']
        fp = adapter.get_footprint(ref)
        if fp is None:
            logger.warning(f"Компонент {ref} не найден, пропуск")
            continue

        # Определяем исходный слой
        orig_layer_str = item.get('original_layer', 'F.Cu')
        if 'B.Cu' in orig_layer_str:
            orig_layer = BoardLayer.BL_B_Cu
        else:
            orig_layer = BoardLayer.BL_F_Cu

        # Если текущий слой отличается от исходного — делаем флип
        if fp.layer != orig_layer:
            logger.debug(f"Возвращаем {ref} на слой {orig_layer_str} (флип)")
            adapter.flip_selected([fp])
            # После флипа перечитываем футпринт (обновляем объект)
            fp = adapter.get_footprint(ref)
            if fp is None:
                continue

        # Восстанавливаем позицию и угол
        orig_x = item['original_position']['x']
        orig_y = item['original_position']['y']
        orig_angle = item['original_angle_deg']
        fp.position = Vector2.from_xy(int(orig_x), int(orig_y))
        fp.orientation = Angle.from_degrees(orig_angle)
        adapter.update_items([fp])
        logger.debug(f"Восстановлен {ref} на позицию ({orig_x/MM:.3f}, {orig_y/MM:.3f}), угол {orig_angle:.1f}°")

    # 2. Удалить созданные via (по UUID)
    for via_data in data.get('created_vias', []):
        uuid_str = via_data.get('uuid')
        if uuid_str:
            try:
                from kipy.proto.common.types import base_types_pb2 as common_types_pb2
                kiid = common_types_pb2.KIID()
                kiid.value = uuid_str
                board.remove_items_by_id([kiid])
                logger.debug(f"Удалена via с UUID {uuid_str}")
            except Exception as e:
                logger.warning(f"Не удалось удалить via {uuid_str}: {e}")

    # 2b. Удалить созданные треки (по UUID) — треки не двигались, им,
    # в отличие от компонентов, восстанавливать нечего, только удалить,
    # ровно как via.
    for track_data in data.get('created_tracks', []):
        uuid_str = track_data.get('uuid')
        if uuid_str:
            try:
                from kipy.proto.common.types import base_types_pb2 as common_types_pb2
                kiid = common_types_pb2.KIID()
                kiid.value = uuid_str
                board.remove_items_by_id([kiid])
                logger.debug(f"Удалён трек с UUID {uuid_str}")
            except Exception as e:
                logger.warning(f"Не удалось удалить трек {uuid_str}: {e}")

    # 3. Удалить файл операции (чтобы не откатывать дважды)
    try:
        json_path.unlink()
        logger.debug(f"Файл {json_path.name} удалён.")
    except Exception as e:
        logger.warning(f"Не удалось удалить файл {json_path.name}: {e}")

    return True