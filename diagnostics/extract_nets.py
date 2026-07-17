import sys
import kipy
from kipy.board_types import Group, FootprintInstance, Pad


def get_selected_footprints_with_nets():
    kicad = kipy.KiCad()
    board = kicad.get_board()

    selection = board.get_selection()
    if not selection:
        print("В KiCad ничего не выделено!")
        return []

    # 1. UUID выделения — с учётом Group (её .items с сервера пустой,
    #    реальные участники лежат в .proto.items, как вы и нашли).
    selected_uuids = set()
    for item in selection:
        if isinstance(item, Group):
            for kiid in item.proto.items:
                selected_uuids.add(str(kiid.value))
        elif hasattr(item, "id") and hasattr(item.id, "value"):
            selected_uuids.add(str(item.id.value))

    # 2. Берём все футпринты и фильтруем по выделенным UUID.
    all_footprints = board.get_footprints()
    selected_fps = [
        fp for fp in all_footprints
        if str(fp.id.value) in selected_uuids
    ]
    return selected_fps


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    fps = get_selected_footprints_with_nets()
    print(f"Найдено выделенных компонентов: {len(fps)}")
    print("=" * 90)

    for idx, fp in enumerate(fps, start=1):
        ref = fp.reference_field.text.value
        val = fp.value_field.text.value
        x = fp.position.x / 1_000_000.0
        y = fp.position.y / 1_000_000.0
        angle = fp.orientation.degrees  # то самое, что не нашлось — просто fp.orientation

        # Собственные пады футпринта — напрямую, без геометрического
        # угадывания "ближайшего" компонента по координатам (это может
        # ошибиться, если два компонента стоят вплотную друг к другу).
        pads = [item for item in fp.definition.items if isinstance(item, Pad)]
        nets = sorted({p.net.name for p in pads if p.net and p.net.name})

        print(f"[{idx}] {ref:<6} | {val:<15} | X:{x:>7.3f}мм  Y:{y:>7.3f}мм  угол:{angle:>6.1f}°")
        if nets:
            print(f"     └── Nets: {', '.join(nets)}")
        else:
            print(f"     └── Nets: (нет подключённых цепей / N/C)")
        print()
