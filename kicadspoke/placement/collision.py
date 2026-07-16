# kicadspoke/placement/collision.py

"""
* _radius_from_bbox – вычисление радиуса как половина диагонали.
* compute_radii – батч-запрос радиусов для списка футпринтов.
* footprints_overlap – проверка перекрытия двух кругов.
* check_collisions – основная функция, проверяет коллизии между перемещаемыми и неперемещаемыми компонентами
"""

import logging
import math
from typing import List, Tuple, Set, Dict, Optional
from kipy.board_types import FootprintInstance
from kipy.geometry import Vector2

from .planner import MoveCommand
from ..utils.units import MM

logger = logging.getLogger(__name__)

DEFAULT_RADIUS_MM = 2.0  # запасной вариант, если bounding box недоступен


def _radius_from_bbox(bbox) -> float:
    """Половина диагонали bounding box'а, в нанометрах. None -> запасной радиус."""
    if bbox is None:
        return DEFAULT_RADIUS_MM * MM
    return 0.5 * math.hypot(bbox.size.x, bbox.size.y)


def compute_radii(footprints: List[FootprintInstance], adapter) -> Dict[str, float]:
    """
    Считает радиусы (нм) для списка футпринтов ОДНИМ батч-запросом через
    adapter.get_bounding_boxes(), вместо обращения к несуществующим
    fp.getBoundingBox()/fp.size (см. ниже — это и было причиной, почему
    раньше ВСЕГДА использовался жёстко заданный запасной радиус 2мм для
    абсолютно всех компонентов, включая крупные 4.7uF и сам IC1).

    ИСПРАВЛЕНО (2026-07-12): в реальном API kicad-python 0.7.1 у
    FootprintInstance нет ни .getBoundingBox(), ни .size — есть только
    attributes, datasheet_field, definition, description_field, id,
    layer, locked, orientation, position, proto, reference_field,
    sheet_path, texts_and_fields, value_field (проверено через dir()).
    Реальный размер даёт только Board.get_item_bounding_box().
    """
    if not footprints:
        return {}
    bboxes = adapter.get_bounding_boxes(footprints)
    radii = {}
    for fp, bbox in zip(footprints, bboxes):
        ref = fp.reference_field.text.value
        radii[ref] = _radius_from_bbox(bbox)
        if bbox is None:
            logger.debug(f"  {ref}: bounding box недоступен, использую запасной радиус {DEFAULT_RADIUS_MM}мм")
    return radii


def footprints_overlap(pos1: Vector2, r1: float, pos2: Vector2, r2: float,
                       margin_mm: float = 0.2) -> bool:
    """Проверяет, перекрываются ли два круга-приближения с заданными позициями/радиусами."""
    dist = (pos1 - pos2).length()
    return dist < (r1 + r2 + margin_mm * MM)


def check_collisions(moves: List[MoveCommand],
                     all_footprints: List[FootprintInstance],
                     adapter,
                     ignore_refs: Set[str] = None,
                     margin_mm: float = 0.2) -> List[Tuple[str, str, float]]:
    """
    Проверяет коллизии между перемещаемыми конденсаторами и другими
    компонентами, используя РЕАЛЬНЫЕ размеры (через adapter.get_bounding_boxes),
    а не фиксированный радиус для всех.

    Возвращает список кортежей (ref1, ref2, расстояние_мм) для всех
    конфликтных пар.
    """
    if ignore_refs is None:
        ignore_refs = set()

    conflicts = []
    move_positions = {m.ref: m.position for m in moves}
    move_refs = set(move_positions.keys())

    relevant_footprints = [fp for fp in all_footprints
                            if fp.reference_field.text.value not in ignore_refs]
    radii = compute_radii(relevant_footprints, adapter)

    fp_by_ref = {fp.reference_field.text.value: fp for fp in relevant_footprints}

    checked_pairs = set()

    for move in moves:
        ref = move.ref
        new_pos = move.position
        r_move = radii.get(ref, DEFAULT_RADIUS_MM * MM)

        # С неперемещаемыми компонентами
        for other_ref, other_fp in fp_by_ref.items():
            if other_ref == ref or other_ref in move_refs:
                continue
            other_pos = other_fp.position
            other_r = radii.get(other_ref, DEFAULT_RADIUS_MM * MM)
            if footprints_overlap(new_pos, r_move, other_pos, other_r, margin_mm):
                dist_mm = (new_pos - other_pos).length() / MM
                conflicts.append((ref, other_ref, dist_mm))

        # С другими перемещаемыми (каждую пару проверяем один раз)
        for other_move in moves:
            other_ref = other_move.ref
            if other_ref == ref:
                continue
            pair = tuple(sorted((ref, other_ref)))
            if pair in checked_pairs:
                continue
            checked_pairs.add(pair)
            other_r = radii.get(other_ref, DEFAULT_RADIUS_MM * MM)
            if footprints_overlap(new_pos, r_move, other_move.position, other_r, margin_mm):
                dist_mm = (new_pos - other_move.position).length() / MM
                conflicts.append((ref, other_ref, dist_mm))

    return conflicts
