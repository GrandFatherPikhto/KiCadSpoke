# kicadstamp/placement/commands.py

from dataclasses import dataclass
from typing import Optional

from kipy.board_types import BoardLayer
from kipy.geometry import Vector2, Angle

from ..constants import SPOKE_LEVEL_ROLE_PLACEHOLDER


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
    registry_key: Optional[str] = None  # see registry.py — None means "not participating in the registry"


@dataclass
class TrackCommand:
    start: Vector2
    end: Vector2
    width_mm: float
    net_name: str
    layer: BoardLayer
    owner_ref: str
    registry_key: Optional[str] = None  # see registry.py — None means "not participating in the registry"


@dataclass
class PlacedComponentInfo:
    """
    Information about one placed component — carried over from planner.py
    (via ManualPositionCalculator) to via_planner.py (for keepout and
    skip_existing_components idempotency). Vias are no longer here —
    all vias (spoke-level and component-level) are now pure geometry,
    computed directly as ViaCommand in the same pass as the position
    (see geometry/spoke_layout.py, ComponentPool is consumed once).
    """
    ref: str
    dest: Vector2
    angle_deg: float
    # Layer of THIS component (per-placement side of ClonePlacement).
    # None = inherit the planner's global target_layer.
    layer: Optional[BoardLayer] = None


def make_registry_key(anchor_id: str, template_name: str, role: Optional[str], index: int) -> str:
    """Build a composite registry key for vias/tracks.

    ``anchor_id`` — e.g. ``"pad:{pad}"`` or ``"thermal:{name}"``.
    ``template_name`` — the spoke/clone template name.
    ``role`` — component role (unique within template) or ``None`` for
    spoke‑level vias/tracks (substituted by :data:`SPOKE_LEVEL_ROLE_PLACEHOLDER`).
    ``index`` — 0‑based index within the specific list (vias or tracks).
    """
    role_part = role if role is not None else SPOKE_LEVEL_ROLE_PLACEHOLDER
    return f"{anchor_id}|{template_name}|{role_part}|{index}"
