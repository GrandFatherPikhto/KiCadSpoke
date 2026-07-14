# decap_placer/placement/services/spacing_relaxer.py
import logging
from typing import List, Tuple
from kipy.geometry import Vector2

from ...config import Config, SpokeComponent
from ...geometry.relax import relax_positions
from ...utils.units import MM
from ...kicad.adapter import KiCadBoardAdapter

logger = logging.getLogger(__name__)

class SpacingRelaxer:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config
        self.min_row_spacing_mm = config.min_row_spacing_mm

    def relax(
        self,
        placements: List[Tuple[SpokeComponent, Vector2, Tuple[float, float], float]]
    ) -> List[Tuple[Vector2, Tuple[SpokeComponent, Tuple[float, float], float]]]:
        """
        Принимает список (component, dest, direction, angle)
        Возвращает список (new_pos, (component, direction, angle))
        """
        entries = [(dest, direction, (component, direction, angle)) for component, dest, direction, angle in placements]
        relaxed = relax_positions(entries, self.min_row_spacing_mm, MM)
        # relaxed уже имеет вид [(new_pos, (component, direction, angle)), ...]
        return relaxed