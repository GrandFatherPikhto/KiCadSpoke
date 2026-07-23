import kipy
from kipy.board_types import FootprintInstance

kc = kipy.KiCad()
board = kc.get_board()
footprints = board.get_footprints()

for fp in footprints:
    ref = fp.reference_field.text.value
    sp = fp.sheet_path
    # Попытка получить прото-представление
    try:
        proto_str = str(sp.proto)
    except AttributeError:
        proto_str = "(no proto)"
    print(f"{ref:10s}  proto={proto_str}")