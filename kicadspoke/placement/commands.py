# kicadspoke/placement/commands.py
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

@dataclass
class PlacedComponentInfo:
    """
    Информация об одном размещённом компоненте — переносится из planner.py
    (через ManualPositionCalculator) в via_planner.py (для keepout и
    идемпотентности skip_existing_components). Via здесь больше нет —
    все via (уровня спицы и уровня компонента) теперь чистая геометрия,
    вычисляются сразу как ViaCommand в том же проходе, что и позиция
    (см. geometry/spoke_layout.py, ComponentPool потребляется один раз).
    """
    ref: str
    dest: Vector2
    angle_deg: float