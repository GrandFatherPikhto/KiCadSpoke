import kipy
kc = kipy.KiCad()
board = kc.get_board()
footprints = board.get_footprints()

for fp in footprints:
    ref = fp.reference_field.text.value
    sp = fp.sheet_path
    # path — это кортеж UUID? Или список объектов?
    print(f"{ref:10s}  path={sp.path!r}")  # посмотрим, что там
    # если это список UUID, можно попробовать вывести их строковое представление