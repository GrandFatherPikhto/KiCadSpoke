import sys
import kipy
from kipy.board_types import Group, Pad

MM = 1_000_000.0


def get_selected_footprints_data():
    """
    Возвращает список словарей с данными выделенных footprint'ов, включая
    их пады.
    Формат:
    [{
        'ref': 'C403', 'value': '1uF', 'footprint': '...',
        'x_mm': 112.9, 'y_mm': 36.5, 'angle_deg': 0.0,
        'nets': ['+2V5', 'GND'],
        'pads': [
            {'number': '1', 'net': '+2V5', 'x_mm': 112.5, 'y_mm': 36.5, 'width_mm': 0.6, 'height_mm': 0.6},
            {'number': '2', 'net': 'GND',  'x_mm': 113.3, 'y_mm': 36.5, 'width_mm': 0.6, 'height_mm': 0.6},
        ],
    }, ...]
    """
    kicad = kipy.KiCad()
    board = kicad.get_board()

    selection = board.get_selection()
    if not selection:
        return []

    # 1. UUID выделения — с учётом Group (у нее .items с сервера пустой,
    #    реальные участники лежат в .proto.items).
    selected_uuids = set()
    for item in selection:
        if isinstance(item, Group):
            for kiid in item.proto.items:
                selected_uuids.add(str(kiid.value))
        elif hasattr(item, "id") and hasattr(item.id, "value"):
            selected_uuids.add(str(item.id.value))

    all_footprints = board.get_footprints()

    selected_fps = [fp for fp in all_footprints if str(fp.id.value) in selected_uuids]

    # Размеры футпринтов — одним батч-запросом на все выделенные разом,
    # а не по одному (см. Board.get_item_bounding_box: для СПИСКА элементов
    # возвращает список Box2|None, для одного элемента — просто Box2|None).
    # include_text=False (по умолчанию) — считаем физический контур
    # (пады + графика), без учёта надписей на шёлкографии.
    bboxes = board.get_item_bounding_box(selected_fps) if selected_fps else []

    result_data = []
    for fp, bbox in zip(selected_fps, bboxes):
        try:
            ref = fp.reference_field.text.value
            val = fp.value_field.text.value
            fp_name = str(fp.definition.id)
        except Exception:
            ref, val, fp_name = "Err", "Err", "Err"

        x = fp.position.x / MM
        y = fp.position.y / MM
        angle = fp.orientation.degrees

        # Собственные пады футпринта — напрямую, без угадывания по
        # ближайшим координатам (может ошибиться, если компоненты стоят
        # вплотную друг к другу).
        pads_data = []
        for item in fp.definition.items:
            if not isinstance(item, Pad):
                continue
            net_name = item.net.name if item.net else ""

            # Размер пада — берём первый медный слой падстека (для
            # обычных однослойных SMD-пад он один; если слоёв несколько,
            # это тот же приём, что и в geometry/thermal_grid.py).
            copper_layers = item.padstack.copper_layers
            if copper_layers:
                pad_w = copper_layers[0].size.x / MM
                pad_h = copper_layers[0].size.y / MM
            else:
                pad_w = pad_h = None

            pads_data.append({
                "number": item.number,
                "net": net_name,
                "x_mm": item.position.x / MM,
                "y_mm": item.position.y / MM,
                "width_mm": pad_w,
                "height_mm": pad_h,
            })

        nets = sorted({p["net"] for p in pads_data if p["net"]})

        if bbox is not None:
            width_mm = bbox.size.x / MM
            height_mm = bbox.size.y / MM
        else:
            width_mm = height_mm = None  # bounding box недоступен для этого элемента

        result_data.append({
            "ref": ref,
            "value": val,
            "footprint": fp_name,
            "x_mm": x,
            "y_mm": y,
            "angle_deg": angle,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "nets": nets,
            "pads": pads_data,
        })

    return result_data


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    components = get_selected_footprints_data()

    print(f"Успешно извлечено данных: {len(components)} шт.\n")
    print("=" * 90)

    for idx, comp in enumerate(components, start=1):
        if comp["width_mm"] is not None:
            size_str = f"{comp['width_mm']:.3f}x{comp['height_mm']:.3f}мм"
        else:
            size_str = "?x?мм (bbox недоступен)"
        print(f"[{idx}] {comp['ref']:<6} | {comp['value']:<15} | "
              f"X:{comp['x_mm']:>7.3f}мм  Y:{comp['y_mm']:>7.3f}мм  "
              f"угол:{comp['angle_deg']:>6.1f}°  размер:{size_str}")
        print(f"     FP: {comp['footprint']}")
        nets_str = ", ".join(comp["nets"]) if comp["nets"] else "(нет подключённых цепей / N/C)"
        print(f"     └── Nets: {nets_str}")
        for pad in comp["pads"]:
            if pad["width_mm"] is not None:
                pad_size_str = f"{pad['width_mm']:.2f}x{pad['height_mm']:.2f}мм"
            else:
                pad_size_str = "?x?"
            print(f"         pad {pad['number']:<4} net={pad['net'] or '?':<15} "
                  f"X:{pad['x_mm']:>7.3f}мм  Y:{pad['y_mm']:>7.3f}мм  размер:{pad_size_str}")
        print()