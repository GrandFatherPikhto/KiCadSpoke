# kicadstamp/placement/executor/base.py
from kipy.board_types import BoardLayer

def layer_to_str(layer) -> str:
    """Convert BoardLayer to string 'F.Cu' or 'B.Cu'."""
    return "B.Cu" if layer == BoardLayer.BL_B_Cu else "F.Cu"