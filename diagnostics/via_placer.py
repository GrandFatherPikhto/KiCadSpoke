#!/usr/bin/env python3
"""
via_placer.py — создание переходных отверстий (via) возле GND-падов компонентов
с использованием геометрических утилит decap_placer.

Поддерживает три режима offset_from:
  - center   – отступ от центра пада по заданному углу
  - edge     – отступ от края пада (с учётом поворота) + дополнительный зазор
  - courtyard – отступ от края Courtyard (с учётом поворота) + дополнительный зазор

Если угол не задан, он вычисляется автоматически:
  - для courtyard: направление к центру Courtyard (по его AABB)
  - для edge: направление от центра пада к центру платы (или к центру первой зоны)

Использует:
  - KiCadBoardAdapter из decap_placer (устойчивый к сбоям IPC)
  - build_keepout / find_free_point для избегания коллизий
  - ray_boundary_distance для точного пересечения с Courtyard
  - транзакции с повторными попытками (commit_with_retry)
"""

import argparse
import math
import logging
import sys
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

import yaml
from kipy.board_types import BoardLayer, FootprintInstance, Pad, Via, Net
from kipy.geometry import Vector2

# Добавляем родительскую папку в sys.path для импорта decap_placer
sys.path.insert(0, str(Path(__file__).parent.parent))

from decap_placer.kicad.adapter import KiCadBoardAdapter
from decap_placer.geometry.boundary import ray_boundary_distance
from decap_placer.geometry.keepout import build_keepout, find_free_point
from decap_placer.utils.units import MM
from decap_placer.exceptions import PlacerError

logger = logging.getLogger(__name__)


# ---------- Геометрические утилиты ----------

def intersect_ray_with_rotated_rect(origin: Vector2, dir_x: float, dir_y: float,
                                    rect_center: Vector2, width_mm: float, height_mm: float,
                                    angle_deg: float) -> Optional[float]:
    """
    Возвращает расстояние от origin до пересечения луча (origin + t * (dir_x, dir_y))
    с прямоугольником шириной width_mm и высотой height_mm,
    центрированным в rect_center и повёрнутым на angle_deg (градусы).
    Возвращает None, если пересечения нет.
    Все координаты в нанометрах, dir_x, dir_y — безразмерные.
    """
    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    # Смещение от центра прямоугольника
    dx = origin.x - rect_center.x
    dy = origin.y - rect_center.y
    # Поворачиваем на -angle
    local_ox = dx * cos_a + dy * sin_a
    local_oy = -dx * sin_a + dy * cos_a

    # Направление в локальных координатах
    local_dx = dir_x * cos_a + dir_y * sin_a
    local_dy = -dir_x * sin_a + dir_y * cos_a

    if abs(local_dx) < 1e-12 and abs(local_dy) < 1e-12:
        return None

    half_w = (width_mm / 2.0) * MM
    half_h = (height_mm / 2.0) * MM

    t_min = -float('inf')
    t_max = float('inf')

    if abs(local_dx) > 1e-12:
        t1 = (-half_w - local_ox) / local_dx
        t2 = (half_w - local_ox) / local_dx
        t_min = max(t_min, min(t1, t2))
        t_max = min(t_max, max(t1, t2))
    else:
        if not (-half_w <= local_ox <= half_w):
            return None

    if abs(local_dy) > 1e-12:
        t1 = (-half_h - local_oy) / local_dy
        t2 = (half_h - local_oy) / local_dy
        t_min = max(t_min, min(t1, t2))
        t_max = min(t_max, max(t1, t2))
    else:
        if not (-half_h <= local_oy <= half_h):
            return None

    if t_max < t_min:
        return None

    if t_min > 0:
        return t_min
    if t_max > 0:
        return t_max
    return None


def get_courtyard_polygon(fp: FootprintInstance) -> List[Vector2]:
    """
    Собирает все точки контура Courtyard (слои F.Courtyard / B.Courtyard)
    из графических элементов футпринта. Возвращает список Vector2 (в нанометрах)
    в глобальной системе координат.
    """
    courtyard_layers = (BoardLayer.BL_F_CrtYd, BoardLayer.BL_B_CrtYd)
    points = []
    for item in fp.definition.items:
        if not hasattr(item, 'layer'):
            continue
        if item.layer not in courtyard_layers:
            continue
        # Извлекаем точки (если есть)
        if hasattr(item, 'points'):
            for pt in item.points:
                points.append(Vector2.from_xy(pt.x, pt.y))
        elif hasattr(item, 'start') and hasattr(item, 'end'):
            points.append(Vector2.from_xy(item.start.x, item.start.y))
            points.append(Vector2.from_xy(item.end.x, item.end.y))
    return points


def compute_ideal_position(board, fp: FootprintInstance, gnd_pad: Pad,
                           offset_from: str, offset_mm: float, angle_deg: Optional[float],
                           courtyard_polygon: Optional[List[Vector2]] = None,
                           plate_center: Optional[Vector2] = None) -> Tuple[Vector2, float, Tuple[float, float]]:
    """
    Вычисляет идеальную позицию via (Vector2 в нанометрах),
    использованный угол (градусы) и направление (dir_x, dir_y).
    """
    pad_pos = gnd_pad.position
    pad_size_mm = get_pad_size_mm(gnd_pad)
    pad_angle_deg = gnd_pad.padstack.angle.degrees

    # Автоугол, если не задан
    if angle_deg is None:
        if offset_from == 'courtyard' and courtyard_polygon and courtyard_polygon:
            cx = sum(p.x for p in courtyard_polygon) / len(courtyard_polygon)
            cy = sum(p.y for p in courtyard_polygon) / len(courtyard_polygon)
            dx = cx - pad_pos.x
            dy = cy - pad_pos.y
            if abs(dx) > 1e-3 or abs(dy) > 1e-3:
                angle_deg = math.degrees(math.atan2(dy, dx))
            else:
                angle_deg = 0.0
        elif offset_from == 'edge' and plate_center is not None:
            dx = plate_center.x - pad_pos.x
            dy = plate_center.y - pad_pos.y
            if abs(dx) > 1e-3 or abs(dy) > 1e-3:
                angle_deg = math.degrees(math.atan2(dy, dx))
            else:
                angle_deg = 0.0
        else:
            angle_deg = 0.0

    rad = math.radians(angle_deg)
    dir_x = math.cos(rad)
    dir_y = math.sin(rad)

    if offset_from == 'center':
        offset_nm = offset_mm * MM
        ideal_pos = Vector2.from_xy(
            int(pad_pos.x + dir_x * offset_nm),
            int(pad_pos.y + dir_y * offset_nm)
        )
        return ideal_pos, angle_deg, (dir_x, dir_y)

    elif offset_from == 'edge':
        if pad_size_mm is None:
            logger.warning(f"Не удалось получить размер пада {gnd_pad.number}, использую offset от центра")
            offset_nm = offset_mm * MM
            ideal_pos = Vector2.from_xy(
                int(pad_pos.x + dir_x * offset_nm),
                int(pad_pos.y + dir_y * offset_nm)
            )
            return ideal_pos, angle_deg, (dir_x, dir_y)

        w_mm, h_mm = pad_size_mm
        t = intersect_ray_with_rotated_rect(
            origin=pad_pos,
            dir_x=dir_x,
            dir_y=dir_y,
            rect_center=pad_pos,
            width_mm=w_mm,
            height_mm=h_mm,
            angle_deg=pad_angle_deg
        )
        if t is None:
            logger.warning(f"Луч не пересекает пад {gnd_pad.number}, использую offset от центра")
            offset_nm = offset_mm * MM
            ideal_pos = Vector2.from_xy(
                int(pad_pos.x + dir_x * offset_nm),
                int(pad_pos.y + dir_y * offset_nm)
            )
            return ideal_pos, angle_deg, (dir_x, dir_y)

        total_offset_nm = t + offset_mm * MM
        ideal_pos = Vector2.from_xy(
            int(pad_pos.x + dir_x * total_offset_nm),
            int(pad_pos.y + dir_y * total_offset_nm)
        )
        return ideal_pos, angle_deg, (dir_x, dir_y)

    elif offset_from == 'courtyard':
        if not courtyard_polygon:
            logger.warning("Courtyard не найден, переключаюсь на режим edge")
            return compute_ideal_position(board, fp, gnd_pad, 'edge', offset_mm, angle_deg,
                                          courtyard_polygon, plate_center)

        try:
            far_point = Vector2.from_xy(
                int(pad_pos.x + dir_x * 100 * MM),
                int(pad_pos.y + dir_y * 100 * MM)
            )
            t, _ = ray_boundary_distance(pad_pos, far_point, courtyard_polygon)
            total_offset_nm = t + offset_mm * MM
            ideal_pos = Vector2.from_xy(
                int(pad_pos.x + dir_x * total_offset_nm),
                int(pad_pos.y + dir_y * total_offset_nm)
            )
            return ideal_pos, angle_deg, (dir_x, dir_y)
        except Exception as e:
            logger.warning(f"Ошибка при вычислении пересечения с Courtyard: {e}, переключаюсь на edge")
            return compute_ideal_position(board, fp, gnd_pad, 'edge', offset_mm, angle_deg,
                                          courtyard_polygon, plate_center)
    else:
        raise ValueError(f"Неизвестный offset_from: {offset_from}")


def get_pad_size_mm(pad: Pad) -> Optional[Tuple[float, float]]:
    layers = pad.padstack.copper_layers
    if not layers:
        return None
    size = layers[0].size
    return size.x / MM, size.y / MM


# ---------- Основная логика ----------

def create_vias_from_config(config: Dict[str, Any], adapter: KiCadBoardAdapter,
                            dry_run: bool = False) -> bool:
    gnd_net_name = config.get('gnd_net_name', 'GND')
    via_cfg = config.get('via', {})
    offset_from = via_cfg.get('offset_from', 'edge').lower()
    if offset_from not in ('center', 'edge', 'courtyard'):
        raise ValueError(f"offset_from должен быть 'center', 'edge' или 'courtyard', получено '{offset_from}'")

    global_offset_mm = via_cfg.get('offset_mm', 1.0)
    global_angle_deg = via_cfg.get('angle_deg', None)
    drill_mm = via_cfg.get('drill_mm', 0.3)
    diameter_mm = via_cfg.get('diameter_mm', 0.6)
    clearance_mm = via_cfg.get('keepout_clearance_mm', 0.2)
    search_step_mm = via_cfg.get('search_step_mm', 0.1)
    search_max_radius_mm = via_cfg.get('search_max_radius_mm', 3.0)
    search_n_directions = via_cfg.get('search_n_directions', 8)

    components = config.get('components', [])
    if not components:
        logger.error("В конфиге не указан список 'components'.")
        return False

    board = adapter._board
    if board is None:
        raise PlacerError("Нет загруженной платы")

    gnd_net = adapter.get_net_by_name(gnd_net_name)
    if gnd_net is None:
        raise PlacerError(f"Цепь '{gnd_net_name}' не найдена на плате")

    all_footprints = adapter.get_footprints()
    all_vias = list(board.get_vias())

    if all_footprints:
        cx = sum(fp.position.x for fp in all_footprints) / len(all_footprints)
        cy = sum(fp.position.y for fp in all_footprints) / len(all_footprints)
        plate_center = Vector2.from_xy(int(cx), int(cy))
    else:
        plate_center = Vector2.from_xy(0, 0)

    vias_to_create: List[Via] = []
    errors = []

    for item in components:
        ref = item.get('ref')
        if not ref:
            continue
        comp_offset = item.get('offset_mm', global_offset_mm)
        comp_angle = item.get('angle_deg', global_angle_deg)

        logger.info(f"Обработка {ref}...")

        fp = adapter.get_footprint(ref)
        if fp is None:
            errors.append(f"Компонент {ref} не найден.")
            continue

        gnd_pad = None
        for pad in adapter.get_footprint_pads(fp):
            if pad.net and pad.net.name == gnd_net_name:
                gnd_pad = pad
                break
        if gnd_pad is None:
            errors.append(f"У {ref} нет пада с цепью '{gnd_net_name}'.")
            continue

        courtyard_polygon = None
        if offset_from == 'courtyard':
            courtyard_polygon = get_courtyard_polygon(fp)
            if not courtyard_polygon:
                logger.warning(f"У {ref} нет Courtyard, переключение на edge")
                actual_offset_from = 'edge'
            else:
                actual_offset_from = 'courtyard'
        else:
            actual_offset_from = offset_from

        ideal_pos, used_angle, direction = compute_ideal_position(
            board, fp, gnd_pad,
            offset_from=actual_offset_from,
            offset_mm=comp_offset,
            angle_deg=comp_angle,
            courtyard_polygon=courtyard_polygon if actual_offset_from == 'courtyard' else None,
            plate_center=plate_center
        )

        # Строим keepout
        other_fps = [f for f in all_footprints if f.reference_field.text.value != ref]
        bboxes = []
        if other_fps:
            bboxes.extend(adapter.get_bounding_boxes(other_fps))
        if all_vias:
            bboxes.extend(adapter.get_bounding_boxes(all_vias))
        bboxes = [b for b in bboxes if b is not None]

        keepout_rects = build_keepout(bboxes, clearance_mm, mm_per_unit=MM)
        via_radius = (diameter_mm / 2.0) * MM

        # preferred_direction – кортеж (float, float)
        pref_dir = direction

        free_pos = find_free_point(
            ideal=ideal_pos,
            keepout=keepout_rects,
            via_radius=via_radius,
            preferred_direction=pref_dir,
            step_mm=search_step_mm,
            max_radius_mm=search_max_radius_mm,
            mm_per_unit=MM,
            n_directions=search_n_directions
        )

        if free_pos is None:
            errors.append(f"Не удалось найти свободное место для via у {ref} (идеал {ideal_pos.x/MM:.3f}, {ideal_pos.y/MM:.3f})")
            continue

        via = adapter.create_via(free_pos, gnd_net, drill_mm, diameter_mm)
        vias_to_create.append(via)
        logger.info(f"  {ref}: via в ({free_pos.x/MM:.3f}, {free_pos.y/MM:.3f}) мм, угол {used_angle:.1f}°")

    if errors:
        logger.warning("Предупреждения/ошибки при обработке:")
        for e in errors:
            logger.warning(f"  {e}")

    if not vias_to_create:
        logger.info("Нет via для создания.")
        return False

    if dry_run:
        logger.info(f"DRY-RUN: будет создано {len(vias_to_create)} via.")
        return True

    def create_work():
        adapter.create_items(vias_to_create)

    success = adapter.commit_with_retry(f"Создание {len(vias_to_create)} via", create_work, retries=2)
    if success:
        logger.info(f"Успешно создано {len(vias_to_create)} via.")
        return True
    else:
        logger.error("Не удалось создать via.")
        return False


def load_config(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )


def main():
    parser = argparse.ArgumentParser(description="Создание via возле GND-падов с использованием decap_placer.")
    parser.add_argument("config", help="Путь к YAML-конфигурационному файлу")
    parser.add_argument("--dry-run", action="store_true", help="Только показать план, не создавать via")
    parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    parser.add_argument("--timeout-ms", type=int, default=20000, help="Таймаут IPC (мс)")
    parser.add_argument("--batch-size", type=int, default=10, help="Размер батча для коммитов (не используется)")
    args = parser.parse_args()

    setup_logging(args.verbose)

    try:
        config = load_config(args.config)
    except Exception as e:
        logger.error(f"Ошибка загрузки конфига: {e}")
        sys.exit(1)

    try:
        adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
        adapter.refresh_board()
    except Exception as e:
        logger.error(f"Не удалось подключиться к KiCad: {e}")
        sys.exit(1)

    try:
        success = create_vias_from_config(config, adapter, dry_run=args.dry_run)
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.exception(f"Ошибка при создании via: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()