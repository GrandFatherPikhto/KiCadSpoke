# decap_placer/placement/commands.py
from dataclasses import dataclass
from kipy.board_types import BoardLayer
from kipy.geometry import Vector2, Angle

@dataclass
class MoveCommand:
    ref: str
    position: Vector2
    angle: Angle
    layer: BoardLayer

@dataclass
class ViaCommand:
    position: Vector2
    drill_mm: float
    diameter_mm: float
    net_name: str
    owner_ref: str