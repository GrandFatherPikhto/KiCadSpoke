# decap_placer/placement/collision.py

import logging
from typing import List, Tuple, Set
from kipy.board_types import FootprintInstance
from kipy.geometry import Vector2

from .planner import MoveCommand
from ..utils.units import MM

logger = logging.getLogger(__name__)


def get_footprint_radius(fp: FootprintInstance) -> float:
    """
    Возвращает радиус футпринта в нанометрах (половина диагонали bounding box).
    Если размеры не удаётся получить, возвращается 2 мм.
    """
    # Пытаемся получить bounding box через метод getBoundingBox (если есть)
    try:
        bbox = fp.getBoundingBox()
        if bbox:
            w = bbox.width
            h = bbox.height
            return 0.5 * (w ** 2 + h ** 2) ** 0.5
    except AttributeError:
        pass

    # Пытаемся получить размер через атрибут size (если есть)
    try:
        size = fp.size
        if size:
            return 0.5 * (size.x ** 2 + size.y ** 2) ** 0.5
    except AttributeError:
        pass

    # Fallback: 2 мм (характерно для 0603/0805)
    return 2 * MM


def footprints_overlap(fp1: FootprintInstance, pos1: Vector2,
                       fp2: FootprintInstance, pos2: Vector2,
                       margin_mm: float = 0.2) -> bool:
    """
    Проверяет, перекрываются ли два футпринта с заданными позициями.
    Использует радиусы (приблизительно).
    """
    r1 = get_footprint_radius(fp1)
    r2 = get_footprint_radius(fp2)
    dist = (pos1 - pos2).length()
    return dist < (r1 + r2 + margin_mm * MM)


def check_collisions(moves: List[MoveCommand],
                     all_footprints: List[FootprintInstance],
                     ignore_refs: Set[str] = None,
                     margin_mm: float = 0.2) -> List[Tuple[str, str, float]]:
    """
    Проверяет коллизии между перемещаемыми конденсаторами и другими компонентами.

    Возвращает список кортежей (ref1, ref2, расстояние_мм) для всех конфликтных пар.
    """
    if ignore_refs is None:
        ignore_refs = set()

    conflicts = []
    move_positions = {m.ref: m.position for m in moves}
    move_refs = set(move_positions.keys())

    fp_by_ref = {fp.reference_field.text.value: fp for fp in all_footprints
                 if fp.reference_field.text.value not in ignore_refs}

    for move in moves:
        ref = move.ref
        new_pos = move.position
        fp = fp_by_ref.get(ref)
        if fp is None:
            logger.debug(f"Не найден футпринт для {ref} при проверке коллизий")
            continue

        # Проверяем с неперемещаемыми компонентами
        for other_ref, other_fp in fp_by_ref.items():
            if other_ref == ref:
                continue
            if other_ref in move_refs:
                continue
            other_pos = other_fp.position
            if footprints_overlap(fp, new_pos, other_fp, other_pos, margin_mm):
                dist_mm = (new_pos - other_pos).length() / MM
                conflicts.append((ref, other_ref, dist_mm))

        # Проверяем с другими перемещаемыми
        for other_move in moves:
            if other_move.ref == ref:
                continue
            other_ref = other_move.ref
            other_fp = fp_by_ref.get(other_ref)
            if other_fp is None:
                continue
            other_pos = other_move.position
            if footprints_overlap(fp, new_pos, other_fp, other_pos, margin_mm):
                dist_mm = (new_pos - other_pos).length() / MM
                conflicts.append((ref, other_ref, dist_mm))

    return conflicts