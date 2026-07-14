#!/usr/bin/env python3
"""
KiCadDecapPlacer — расстановка развязывающих конденсаторов вокруг
периферийного/BGA-компонента (например, FPGA) через KiCad IPC API
(kicad-python), с поддержкой:
  - трёх режимов размещения относительно границы Rule Area: outside,
    inside, boundary (inside имеет смысл для BGA; для периметрийных
    корпусов вроде TQFP используйте outside/boundary);
  - опциональной stitching-виа на GND (или любую другую цепь) рядом с
    каждым конденсатором;
  - независимого генератора массива термопереходов (thermal via array)
    внутри площадки термопада (EP) — актуально для TQFP/QFN с открытым
    металлическим дном под GND, где сам конденсатор не нужен, а нужна
    решётка виа для отвода тепла и связи с внутренним слоем земли.

Секция thermal_via_array не зависит от rules/boundary_zone — можно
использовать конфиг только с ней, без расстановки конденсаторов вовсе.

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


def mirror_corrected_angle_deg(phi_deg, layer):
    """
    Корректирует угол φ (посчитанный в "обычных", не зеркальных
    координатах — например, вдоль луча центр->площадка) под слой, на
    котором окажется компонент.

    ПРОВЕРЕНО ЭМПИРИЧЕСКИ (2026-07-12, тест флипа): у зеркального
    (обратная сторона, B.Cu) футпринта локальная ось X тоже отражена,
    поэтому чтобы компонент физически смотрел в направлении φ в
    АБСОЛЮТНЫХ координатах платы, угол нужно ставить 180°-φ, а не φ.
    Флип симметричного 0603 с φ=0° дал ровно 180°, что и подтверждает
    формулу. Без этой поправки получался "ёжик" — у каждого конденсатора
    угол был неверным на свою величину, зависящую от его собственного φ.
    """
    if layer == BoardLayer.BL_B_Cu:
        return 180.0 - phi_deg
    return phi_deg


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


def get_pad_size(pad):
    """Возвращает (width, height) площадки в её локальной (неповёрнутой)
    системе координат, во внутренних единицах. Берёт первый доступный
    медный слой падстека — для простых SMD-площадок (как термопад TQFP)
    он один."""
    layers = pad.padstack.copper_layers
    if not layers:
        raise ValueError("у площадки нет медных слоёв в падстеке — не могу определить размер")
    size = layers[0].size
    return size.x, size.y


def compute_thermal_via_grid(pad, rows: int, cols: int, margin_mm: float, stagger: bool = False):
    """
    Строит сетку из rows x cols точек внутри площадки термопада (EP),
    отступив margin_mm от каждого края, с учётом реального поворота
    площадки (pad.padstack.angle — она уже включает поворот футпринта,
    т.к. KiCad держит её синхронизированной при повороте компонента).

    stagger=True сдвигает нечётные строки на половину шага по X (для более
    равномерного теплоотвода при том же количестве отверстий).

    Возвращает список Vector2 в абсолютных координатах платы.
    """
    if rows < 1 or cols < 1:
        raise ValueError("rows и cols должны быть >= 1")

    width, height = get_pad_size(pad)
    margin = margin_mm * MM
    usable_w = width - 2 * margin
    usable_h = height - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        raise ValueError(
            f"margin_mm={margin_mm} слишком большой для площадки {width/MM:.2f}x{height/MM:.2f} мм"
        )

    # Локальные координаты (до поворота), центр площадки — начало координат.
    local_points = []
    for r in range(rows):
        y = 0 if rows == 1 else -usable_h / 2 + usable_h * r / (rows - 1)
        row_offset = (usable_w / (cols * 2)) if (stagger and cols > 1 and r % 2 == 1) else 0
        for c in range(cols):
            x = 0 if cols == 1 else -usable_w / 2 + usable_w * c / (cols - 1)
            local_points.append((x + row_offset, y))

    angle_rad = pad.padstack.angle.to_radians()
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)

    absolute_points = []
    for lx, ly in local_points:
        rx = lx * cos_a - ly * sin_a
        ry = lx * sin_a + ly * cos_a
        absolute_points.append(Vector2.from_xy(int(pad.position.x + rx), int(pad.position.y + ry)))
    return absolute_points


def plan_thermal_via_array(footprints, nets, cfg, default_target_ref, logger_print=print):
    """
    Собирает план виа для термопада, если в конфиге есть секция
    thermal_via_array с enabled: true. Возвращает список
    (Vector2, drill_mm, diameter_mm, net, owner_ref) — в том же духе, что
    и planned_vias, чтобы main() мог их объединить без специального кода.
    """
    tva_cfg = cfg.get("thermal_via_array")
    if not tva_cfg or not tva_cfg.get("enabled", False):
        return []

    ref = tva_cfg.get("target_ref", default_target_ref)
    fp = find_footprint(footprints, ref)
    if fp is None:
        logger_print(f"[ошибка] thermal_via_array: компонент {ref!r} не найден — пропуск", file=sys.stderr)
        return []

    pad_number = tva_cfg["pad"]
    pad = find_pad(fp, pad_number)
    if pad is None:
        logger_print(f"[ошибка] thermal_via_array: у {ref} нет площадки {pad_number!r} — пропуск", file=sys.stderr)
        return []

    net_name = tva_cfg.get("net", "GND")
    if pad.net.name != net_name:
        logger_print(f"[warn] thermal_via_array: площадка {pad_number} принадлежит цепи "
                      f"{pad.net.name!r}, а в конфиге указана {net_name!r}", file=sys.stderr)
    net = find_net_by_name(nets, net_name)
    if net is None:
        logger_print(f"[ошибка] thermal_via_array: цепь {net_name!r} не найдена на плате — пропуск", file=sys.stderr)
        return []

    try:
        points = compute_thermal_via_grid(
            pad,
            rows=tva_cfg.get("rows", 4),
            cols=tva_cfg.get("cols", 4),
            margin_mm=tva_cfg.get("margin_mm", 0.5),
            stagger=(tva_cfg.get("pattern", "grid") == "staggered"),
        )
    except ValueError as e:
        logger_print(f"[ошибка] thermal_via_array: {e}", file=sys.stderr)
        return []

    drill_mm = tva_cfg.get("drill_mm", 0.3)
    diameter_mm = tva_cfg.get("diameter_mm", 0.5)
    return [(p, drill_mm, diameter_mm, net, ref) for p in points]


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


def plan_vias_for_cap(cap_point, direction, via_cfg, net, placement="outside"):
    """Возвращает список позиций (Vector2) для stitching-виа рядом с
    конденсатором.

    ВАЖНО (2026-07-12, реальный баг с виа на пинах IC1): "от вывода" — это
    РАЗНЫЕ абсолютные направления в зависимости от placement самого
    конденсатора:
      - outside: конденсатор УЖЕ дальше от центра, чем сам вывод (за
        границей зоны), поэтому "дальше от вывода" = ещё дальше наружу,
        вдоль direction (ux, uy).
      - inside: конденсатор МЕЖДУ центром и выводом (ближе к центру), и
        "дальше от вывода" означает УЙТИ К ЦЕНТРУ, т.е. против direction.
    Раньше знак был фиксирован (всегда вдоль direction) — из-за этого у
    inside-конденсаторов виа "away_from_pad" на самом деле уезжала К
    выводу, а не от него, и попадала прямо на площадки IC1.

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
    away_sign = -1.0 if placement == "inside" else 1.0
    offset = via_cfg.get("offset_from_cap_mm", 0.5) * MM
    count = int(via_cfg.get("count", 1))

    if count == 1:
        mode = via_cfg.get("direction", "away_from_pad")
        if mode == "away_from_pad":
            vx, vy = ux * away_sign, uy * away_sign
        elif mode == "toward_pad":
            vx, vy = -ux * away_sign, -uy * away_sign
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
    ap.add_argument("--timeout-ms", type=int, default=20000,
                     help="таймаут ожидания ответа от KiCad, мс (по умолчанию kipy — всего 2000, "
                          "этого мало для большого коммита; см. падение begin_commit с ConnectionError: Timed out)")
    ap.add_argument("--batch-size", type=int, default=10,
                     help="сколько конденсаторов/виа применять за один commit (меньше батч — меньше риск "
                          "потерять всё разом при таймауте/сбое)")
    args = ap.parse_args()

    cfg = load_config(args.config)

    kicad = kipy.KiCad(timeout_ms=args.timeout_ms)
    board = kicad.get_board()

    footprints = list(board.get_footprints())
    zones = list(board.get_zones())
    nets = list(board.get_nets())

    target = find_footprint(footprints, cfg["target_ref"])
    if target is None:
        sys.exit(f"[ошибка] не найден компонент {cfg['target_ref']!r} на плате")

    center = target.position
    layer = LAYER_MAP[cfg.get("side", "back")]
    global_via_cfg = cfg.get("via", {"enabled": False})

    planned_caps = []   # (footprint, dest, angle)
    planned_vias = []   # (Vector2, drill_mm, diameter_mm, net, owner_ref)

    # --- Расстановка конденсаторов + stitching-виа (нужна секция rules) ---
    if cfg.get("rules"):
        boundary = find_boundary_zone(zones, cfg["boundary_zone"])
        if boundary is None:
            sys.exit(f"[ошибка] не найдена зона {cfg['boundary_zone']!r}")
        boundary_pts = polyline_points(boundary.outline.outline)

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

                rotation_mode = cfg.get("rotation_mode", "radial")
                if rotation_mode in ("radial", "orthogonal"):
                    phi_deg = math.degrees(math.atan2(direction[1], direction[0]))
                    if rotation_mode == "orthogonal":
                        # Округляем радиальный угол до ближайших 90° — для
                        # прямоугольной (выровненной по X/Y) Rule Area это
                        # ставит конденсатор параллельно или перпендикулярно
                        # её сторонам вместо "лучей от центра". Работает для
                        # любой стороны зоны без доп. настроек, т.к. зона
                        # уже выровнена по осям.
                        phi_deg = round(phi_deg / 90.0) * 90.0
                    angle = Angle.from_degrees(mirror_corrected_angle_deg(phi_deg, layer))
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
                        drill_mm = via_cfg.get("drill_mm", 0.3)
                        diameter_mm = via_cfg.get("diameter_mm", 0.6)
                        for via_pos in plan_vias_for_cap(dest, direction, via_cfg, via_net, placement=placement):
                            planned_vias.append((via_pos, drill_mm, diameter_mm, via_net, a["ref"]))

    # --- Массив термопереходов под термопадом (независимая секция) ---
    planned_vias.extend(plan_thermal_via_array(footprints, nets, cfg, cfg["target_ref"]))

    print(f"Запланировано перемещений конденсаторов: {len(planned_caps)}")
    for cap, dest, angle in planned_caps:
        ref = cap.reference_field.text.value
        print(f"  {ref}: -> ({dest.x/MM:.3f}, {dest.y/MM:.3f}) мм, угол={angle.degrees:.1f}°")

    print(f"Запланировано виа (stitching + термопереходы): {len(planned_vias)}")
    for pos, drill_mm, diameter_mm, net, owner_ref in planned_vias:
        print(f"  возле {owner_ref}: ({pos.x/MM:.3f}, {pos.y/MM:.3f}) мм, "
              f"net={net.name}, d={diameter_mm}мм/сверло={drill_mm}мм")

    if args.dry_run:
        print("[dry-run] изменения не применены")
        return

    def commit_batch(description, work_fn):
        """Оборачивает work_fn в отдельный begin_commit/push_commit.
        При ошибке — drop_commit и False, не поднимая исключение дальше:
        так один неудачный батч не рушит уже применённые предыдущие."""
        commit = board.begin_commit()
        try:
            work_fn()
            board.push_commit(commit, description)
            return True
        except Exception as e:
            print(f"[ошибка] {description}: {type(e).__name__}: {e}", file=sys.stderr)
            try:
                board.drop_commit(commit)
            except Exception:
                pass
            return False

    batch_size = args.batch_size
    failed_caps, failed_vias = [], []

    # --- Флип на нужную сторону через настоящий GUI-action ---
    # ПРОВЕРЕНО (2026-07-12): простое footprint.layer = ... меняет только
    # поле в данных и НЕ зеркалирует площадки/шёлкографию — визуально
    # компонент остаётся как будто на прежней стороне. Настоящий переворот
    # — это action "pcbnew.InteractiveEdit.flip" (хоткей F в GUI), который
    # работает через ТЕКУЩЕЕ ВЫДЕЛЕНИЕ, а не принимает объекты напрямую.
    # Он меняет layer и добавляет 180° к повороту, позицию НЕ трогает.
    # run_action — не begin_commit/push_commit транзакция, это отдельное
    # GUI-действие со своим undo.
    #
    # КРИТИЧНО: после флипа локальные Python-объекты в planned_caps ещё
    # хранят СТАРЫЕ layer/orientation (с момента исходного get_footprints()
    # при планировании) — если потом пушить их как есть через
    # update_items(), это молча ОТКАТИТ флип обратно. Поэтому после флипа
    # обязательно перечитываем футпринты заново и подменяем объекты в
    # planned_caps на свежие перед тем, как выставлять итоговые position/
    # orientation.
    need_flip = [(cap, dest, angle) for cap, dest, angle in planned_caps if cap.layer != layer]
    if need_flip:
        print(f"\nФлип на {cfg.get('side', 'back')}: {len(need_flip)} конденсаторов")
        flip_batches = [need_flip[i:i + batch_size] for i in range(0, len(need_flip), batch_size)]
        for idx, batch in enumerate(flip_batches, 1):
            try:
                board.clear_selection()
                board.add_to_selection([c for c, _, _ in batch])
                status = kicad.run_action("pcbnew.InteractiveEdit.flip")
                board.clear_selection()
                print(f"  флип-батч {idx}/{len(flip_batches)} ({len(batch)} шт.): {status}")
            except Exception as e:
                print(f"[ошибка] флип-батч {idx}/{len(flip_batches)}: {type(e).__name__}: {e}", file=sys.stderr)
                try:
                    board.clear_selection()
                except Exception:
                    pass

        # Перечитываем футпринты заново — см. предупреждение выше.
        fresh_footprints = list(board.get_footprints())
        fresh_by_ref = {fp.reference_field.text.value: fp for fp in fresh_footprints}
        planned_caps = [
            (fresh_by_ref.get(cap.reference_field.text.value, cap), dest, angle)
            for cap, dest, angle in planned_caps
        ]

    cap_batches = [planned_caps[i:i + batch_size] for i in range(0, len(planned_caps), batch_size)]
    for idx, batch in enumerate(cap_batches, 1):
        def work(batch=batch):
            for cap, dest, angle in batch:
                cap.position = dest
                cap.orientation = angle
                # Слой сюда уже НЕ выставляем — он выставлен флипом выше;
                # прямое присвоение .layer здесь только повторило бы старую
                # проблему (без реального зеркалирования площадок).
            board.update_items([c for c, _, _ in batch])

        desc = f"KiCadDecapPlacer: конденсаторы, батч {idx}/{len(cap_batches)}"
        ok = commit_batch(desc, work)
        print(f"  батч конденсаторов {idx}/{len(cap_batches)} ({len(batch)} шт.): {'OK' if ok else 'ОШИБКА — пропущен'}")
        if not ok:
            failed_caps.extend(c.reference_field.text.value for c, _, _ in batch)

    via_batches = [planned_vias[i:i + batch_size] for i in range(0, len(planned_vias), batch_size)]
    for idx, batch in enumerate(via_batches, 1):
        def work(batch=batch):
            new_vias = [make_via(pos, net, drill_mm, diameter_mm) for pos, drill_mm, diameter_mm, net, _owner in batch]
            board.create_items(new_vias)

        desc = f"KiCadDecapPlacer: виа, батч {idx}/{len(via_batches)}"
        ok = commit_batch(desc, work)
        print(f"  батч виа {idx}/{len(via_batches)} ({len(batch)} шт.): {'OK' if ok else 'ОШИБКА — пропущен'}")
        if not ok:
            failed_vias.extend(owner for _, _, _, _, owner in batch)

    total_batches = len(cap_batches) + len(via_batches)
    print(f"\nГотово: {total_batches} батчей обработано.")
    if failed_caps:
        print(f"[warn] не применены перемещения конденсаторов: {sorted(set(failed_caps))}", file=sys.stderr)
    if failed_vias:
        print(f"[warn] не применены виа рядом с: {sorted(set(failed_vias))}", file=sys.stderr)
    if not failed_caps and not failed_vias:
        print("Все батчи применены без ошибок.")


if __name__ == "__main__":
    main()