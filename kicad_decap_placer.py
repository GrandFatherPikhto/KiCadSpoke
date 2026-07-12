#!/usr/bin/env python3
"""
KiCadDecapPlacer — расстановка развязывающих конденсаторов вокруг BGA-
компонента (например, FPGA) через KiCad IPC API (kicad-python), с
поддержкой:
  - трёх режимов размещения относительно границы Rule Area: outside,
    inside, boundary;
  - опциональной stitching-виа на GND (или любую другую цепь) рядом с
    каждым конденсатором.

Проверено на реальном API kicad-python==0.7.1 (см. пометки ВАЖНО по
тексту — это места, где документация API скуднее кода, и стоит один раз
свериться руками на тестовой плате перед массовым прогоном).

Запуск: открыть плату в KiCad, затем
    python3 kicad_decap_placer.py decap_placement.yaml [--dry-run]
"""

import argparse
import math
import sys

import yaml

import kipy
from kipy.board_types import BoardLayer, Pad, FootprintInstance, Via, ViaType
from kipy.geometry import Vector2, Angle


LAYER_MAP = {"front": BoardLayer.BL_F_Cu, "back": BoardLayer.BL_B_Cu}
MM = 1_000_000  # внутренние единицы Vector2 — нанометры


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_footprint(footprints, ref):
    for fp in footprints:
        if fp.reference_field.text.value == ref:
            return fp
    return None


def find_pad(fp: FootprintInstance, pad_number: str):
    for item in fp.definition.items:
        if isinstance(item, Pad) and item.number == pad_number:
            return item
    return None


def find_boundary_zone(zones, name):
    for z in zones:
        if z.name == name:
            return z
    return None


def find_net_by_name(nets, name):
    for net in nets:
        if net.name == name:
            return net
    return None


def polyline_points(polyline):
    """PolyLine.outline -> список Vector2 (без учёта дуг — для прямоугольной/
    полигональной Rule Area этого обычно достаточно)."""
    return [node.point for node in polyline if node.has_point]


def _ray_segment_intersection(ox, oy, dx, dy, x1, y1, x2, y2):
    ex, ey = x2 - x1, y2 - y1
    denom = dx * ey - dy * ex
    if abs(denom) < 1e-9:
        return None
    t = ((x1 - ox) * ey - (y1 - oy) * ex) / denom
    s = ((x1 - ox) * dy - (y1 - oy) * dx) / denom
    if 0 <= s <= 1:
        return t
    return None


def ray_boundary_distance(center: Vector2, target: Vector2, boundary_pts):
    """Расстояние (внутр. единицы) от center до ближайшего пересечения луча
    center->target с границей полигона. None, если пересечения нет."""
    dx, dy = target.x - center.x, target.y - center.y
    length = math.hypot(dx, dy)
    if length == 0:
        raise ValueError("center и target совпадают — не могу определить направление")
    ux, uy = dx / length, dy / length

    best_t = None
    n = len(boundary_pts)
    for i in range(n):
        p1, p2 = boundary_pts[i], boundary_pts[(i + 1) % n]
        t = _ray_segment_intersection(center.x, center.y, ux, uy, p1.x, p1.y, p2.x, p2.y)
        if t is not None and t > 0 and (best_t is None or t < best_t):
            best_t = t
    return best_t, (ux, uy)


def compute_cap_position(center: Vector2, pad_pos: Vector2, boundary_pts, placement: str, offset_mm: float):
    """
    Считает целевую точку конденсатора вдоль луча center->pad_pos, в
    зависимости от режима placement:
      outside  = граница + offset наружу
      inside   = площадка - offset в сторону границы (между площадкой и
                 границей, ближе к площадке)
      boundary = граница + offset вдоль того же луча (offset обычно 0)
    Возвращает (Vector2, (ux, uy)) — точку и единичный вектор направления,
    последний нужен для ориентации виа/угла компонента.
    """
    t_boundary, (ux, uy) = ray_boundary_distance(center, pad_pos, boundary_pts)
    if t_boundary is None:
        raise ValueError("луч не пересекает границу зоны — проверьте геометрию/сторону")

    offset = offset_mm * MM

    if placement == "outside":
        t = t_boundary + offset
    elif placement == "boundary":
        t = t_boundary + offset
    elif placement == "inside":
        pad_t = math.hypot(pad_pos.x - center.x, pad_pos.y - center.y)
        t = pad_t - offset
        if t < 0:
            raise ValueError(f"offset_mm={offset_mm} больше расстояния до площадки — точка ушла за центр компонента")
    else:
        raise ValueError(f"неизвестный placement: {placement!r} (ожидается outside/inside/boundary)")

    point = Vector2.from_xy(int(center.x + ux * t), int(center.y + uy * t))
    return point, (ux, uy)


def resolve_via_settings(global_via, assignment_via):
    """Сливает глобальный via: {...} с per-assignment переопределением.
    assignment_via может быть: отсутствовать (наследуем всё), bool
    (true = включить с глобальными настройками, false = выключить),
    либо dict с частичным переопределением полей."""
    merged = dict(global_via) if global_via else {"enabled": False}
    if assignment_via is None:
        return merged
    if isinstance(assignment_via, bool):
        merged["enabled"] = assignment_via
        return merged
    if isinstance(assignment_via, dict):
        merged.update(assignment_via)
        return merged
    raise ValueError(f"некорректное значение via: в assignment: {assignment_via!r}")


def make_via(position: Vector2, net, drill_mm: float, diameter_mm: float):
    via = Via()
    via.type = ViaType.VT_THROUGH
    via.position = position
    via.net = net
    via.drill_diameter = int(drill_mm * MM)
    via.diameter = int(diameter_mm * MM)
    return via


def plan_vias_for_cap(cap_point, direction, via_cfg, net):
    """Возвращает список позиций (Vector2) для stitching-виа рядом с
    конденсатором.

    count=1: одна виа смещена от cap_point на offset_from_cap_mm вдоль оси,
             выбранной via.direction (away_from_pad/toward_pad/perpendicular).
    count=2: пара виа, симметричная относительно cap_point, разнесённая
             перпендикулярно ИСХОДНОМУ лучу центр->площадка на
             offset_from_cap_mm в каждую сторону. via.direction в этом
             случае игнорируется — у симметричной пары нет одного
             "направления", а совпадение оси смещения с осью разноса пары
             (например, при direction: perpendicular) давало бы
             несимметричный результат.
    """
    ux, uy = direction
    offset = via_cfg.get("offset_from_cap_mm", 0.5) * MM
    count = int(via_cfg.get("count", 1))

    if count == 1:
        mode = via_cfg.get("direction", "away_from_pad")
        if mode == "away_from_pad":
            vx, vy = ux, uy
        elif mode == "toward_pad":
            vx, vy = -ux, -uy
        elif mode == "perpendicular":
            vx, vy = -uy, ux
        else:
            raise ValueError(f"неизвестный via.direction: {mode!r}")
        return [Vector2.from_xy(int(cap_point.x + vx * offset), int(cap_point.y + vy * offset))]

    elif count == 2:
        px, py = -uy, ux  # перпендикуляр к лучу центр->площадка
        return [
            Vector2.from_xy(int(cap_point.x + px * offset), int(cap_point.y + py * offset)),
            Vector2.from_xy(int(cap_point.x - px * offset), int(cap_point.y - py * offset)),
        ]
    else:
        raise ValueError(f"via.count поддерживает только 1 или 2, получено {count}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--dry-run", action="store_true", help="только посчитать и напечатать, не двигать")
    args = ap.parse_args()

    cfg = load_config(args.config)

    kicad = kipy.KiCad()
    board = kicad.get_board()

    footprints = list(board.get_footprints())
    zones = list(board.get_zones())
    nets = list(board.get_nets())

    target = find_footprint(footprints, cfg["target_ref"])
    if target is None:
        sys.exit(f"[ошибка] не найден компонент {cfg['target_ref']!r} на плате")

    boundary = find_boundary_zone(zones, cfg["boundary_zone"])
    if boundary is None:
        sys.exit(f"[ошибка] не найдена зона {cfg['boundary_zone']!r}")
    boundary_pts = polyline_points(boundary.outline.outline)

    center = target.position
    layer = LAYER_MAP[cfg["side"]]
    global_via_cfg = cfg.get("via", {"enabled": False})

    planned_caps = []   # (footprint, dest, angle)
    planned_vias = []   # (Vector2, via_cfg, net)

    for rule in cfg["rules"]:
        net_name = rule["net"]
        for a in rule["assignments"]:
            pad = find_pad(target, a["pad"])
            if pad is None:
                print(f"[ошибка] у {cfg['target_ref']} нет площадки {a['pad']!r} — пропуск", file=sys.stderr)
                continue
            if pad.net.name != net_name:
                print(f"[warn] площадка {a['pad']} принадлежит цепи {pad.net.name!r}, "
                      f"а в конфиге указана {net_name!r} — проверьте номер площадки", file=sys.stderr)

            cap = find_footprint(footprints, a["ref"])
            if cap is None:
                print(f"[ошибка] конденсатор {a['ref']!r} не найден на плате — пропуск", file=sys.stderr)
                continue

            placement = a.get("placement", "outside")
            offset_mm = a.get("offset_mm", 1.0)

            try:
                dest, direction = compute_cap_position(center, pad.position, boundary_pts, placement, offset_mm)
            except ValueError as e:
                print(f"[ошибка] {a['ref']}: {e}", file=sys.stderr)
                continue

            if cfg.get("rotation_mode", "radial") == "radial":
                angle = Angle.from_degrees(math.degrees(math.atan2(direction[1], direction[0])))
            else:
                angle = Angle.from_degrees(cfg.get("fixed_angle_deg", 0.0))

            planned_caps.append((cap, dest, angle))

            via_cfg = resolve_via_settings(global_via_cfg, a.get("via"))
            if via_cfg.get("enabled", False):
                via_net_name = via_cfg.get("net", "GND")
                via_net = find_net_by_name(nets, via_net_name)
                if via_net is None:
                    print(f"[warn] цепь {via_net_name!r} для виа у {a['ref']} не найдена на плате — виа пропущена", file=sys.stderr)
                else:
                    for via_pos in plan_vias_for_cap(dest, direction, via_cfg, via_net):
                        planned_vias.append((via_pos, via_cfg, via_net, a["ref"]))

    print(f"Запланировано перемещений конденсаторов: {len(planned_caps)}")
    for cap, dest, angle in planned_caps:
        ref = cap.reference_field.text.value
        print(f"  {ref}: -> ({dest.x/MM:.3f}, {dest.y/MM:.3f}) мм, угол={angle.degrees:.1f}°")

    print(f"Запланировано виа: {len(planned_vias)}")
    for pos, via_cfg, net, owner_ref in planned_vias:
        print(f"  возле {owner_ref}: ({pos.x/MM:.3f}, {pos.y/MM:.3f}) мм, "
              f"net={net.name}, d={via_cfg.get('diameter_mm')}мм/сверло={via_cfg.get('drill_mm')}мм")

    if args.dry_run:
        print("[dry-run] изменения не применены")
        return

    commit = board.begin_commit()
    try:
        for cap, dest, angle in planned_caps:
            cap.position = dest
            cap.orientation = angle
            if cap.layer != layer:
                cap.layer = layer  # см. предупреждение из прошлой сессии: сверить визуально с Flip footprint
        board.update_items([c for c, _, _ in planned_caps])

        new_vias = [
            make_via(pos, net, via_cfg.get("drill_mm", 0.3), via_cfg.get("diameter_mm", 0.6))
            for pos, via_cfg, net, _owner in planned_vias
        ]
        if new_vias:
            board.create_items(new_vias)

        board.push_commit(commit, "KiCadDecapPlacer: расстановка развязки + stitching-виа")
        print("Готово, коммит применён.")
    except Exception:
        board.drop_commit(commit)
        raise


if __name__ == "__main__":
    main()
