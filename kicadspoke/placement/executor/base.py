# kicadspoke/placement/executor/base.py
from kipy.board_types import BoardLayer

def layer_to_str(layer) -> str:
    """Преобразует BoardLayer в строку 'F.Cu' или 'B.Cu'."""
    return "B.Cu" if layer == BoardLayer.BL_B_Cu else "F.Cu"