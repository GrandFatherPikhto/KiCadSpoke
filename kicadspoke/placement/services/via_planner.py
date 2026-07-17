# kicadspoke/placement/services/via_planner.py

import logging
from typing import List, Optional, Set, Tuple
from kipy.board_types import FootprintInstance, BoardLayer
from kipy.geometry import Vector2

from ...config import Config
from ...geometry.keepout import Rect, build_keepout, find_free_point
from ...geometry.thermal_grid import compute_thermal_via_grid
from ...kicad.adapter import KiCadBoardAdapter
from ...utils.units import MM
from ...exceptions import GeometryError, ComponentNotFoundError
from ..commands import ViaCommand, PlacedComponentInfo

from ..interfaces import IViaPlanner

logger = logging.getLogger(__name__)


class ViaPlanner(IViaPlanner):
    """
    ИЗМЕНЕНО (KiCadSpoke, обобщённые via): via уровня спицы и уровня
    компонента теперь полностью вычисляются заранее в
    ManualPositionCalculator.compute_raw_positions() (чистая геометрия,
    без обращения к живой плате) — ViaPlanner их больше не считает, а
    только применяет к ним skip_existing_components и добавляет термовиа
    (единственное, что по-прежнему зависит от живой платы — реальный
    термопад целевого компонента).
    """

    _VIA_POSITION_TOLERANCE_NM = 10_000  # 0.01 мм

    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config

    def _via_already_exists(self, existing_vias, position: Vector2, net_name: str) -> bool:
        for via in existing_vias:
            if not via.net or via.net.name != net_name:
                continue
            if (abs(via.position.x - position.x) <= self._VIA_POSITION_TOLERANCE_NM and
                    abs(via.position.y - position.y) <= self._VIA_POSITION_TOLERANCE_NM):
                return True
        return False

    def plan_vias(
        self,
        planned_components: List[PlacedComponentInfo],
        planned_vias: List[ViaCommand],
        target_fp: FootprintInstance,
        target_layer: BoardLayer
    ) -> List[ViaCommand]:
        existing_vias = self.adapter.get_vias() if self.cfg.skip_existing_components else []

        vias: List[ViaCommand] = []
        skipped = 0
        for via in planned_vias:
            if self.cfg.skip_existing_components and self._via_already_exists(
                    existing_vias, via.position, via.net_name):
                skipped += 1
                logger.debug(f"  via для {via.owner_ref}: уже существует, пропущена")
                continue
            vias.append(via)
        if skipped:
            logger.info(f"Пропущено {skipped} via спиц/компонентов, уже существующих на плате")

        keepout = self._build_keepout(target_fp, planned_components)
        logger.debug(f"Keepout для термовиа: {len(keepout)} прямоугольников")
        vias.extend(self._plan_thermal_vias(planned_components, target_fp, keepout, existing_vias))

        logger.info(f"plan_vias завершено: {len(vias)} виа")
        return vias

    def _build_keepout(
        self,
        target_fp: FootprintInstance,
        planned: List[PlacedComponentInfo],
        exclude: Optional[Set[Tuple[str, str]]] = None
    ) -> List[Rect]:
        pad_items = []
        for pad in self.adapter.get_footprint_pads(target_fp):
            if exclude and (self.cfg.target_ref, pad.number) in exclude:
                continue
            pad_items.append(pad)
        for info in planned:
            fp = self.adapter.get_footprint(info.ref)
            if fp is None:
                continue
            for pad in self.adapter.get_footprint_pads(fp):
                if exclude and (info.ref, pad.number) in exclude:
                    continue
                pad_items.append(pad)
        bboxes = self.adapter.get_bounding_boxes(pad_items)
        return build_keepout(bboxes, self.cfg.via_keepout_clearance_mm, mm_per_unit=MM)

    def _plan_thermal_vias(
        self,
        planned: List[PlacedComponentInfo],
        target_fp: FootprintInstance,
        keepout: List[Rect],
        existing_vias: Optional[List] = None
    ) -> List[ViaCommand]:
        existing_vias = existing_vias or []
        tva = self.cfg.thermal_via_array
        if not tva.enabled:
            return []
        logger.debug(f"Планирование термовиа для {tva.target_ref}, площадка {tva.pad}")
        fp = self.adapter.get_footprint(tva.target_ref)
        if fp is None:
            raise ComponentNotFoundError(f"Термопад: компонент {tva.target_ref} не найден")
        pad = self.adapter.get_pad_by_number(fp, tva.pad)
        if pad is None:
            raise ComponentNotFoundError(f"Термопад: у {tva.target_ref} нет площадки {tva.pad}")
        try:
            points = compute_thermal_via_grid(
                pad,
                rows=tva.rows,
                cols=tva.cols,
                margin_mm=tva.margin_mm,
                stagger=(tva.pattern == "staggered")
            )
        except GeometryError as e:
            raise GeometryError(f"Термопад: {e}")

        exclude = {(tva.target_ref, tva.pad)}
        keepout_excl = self._build_keepout(target_fp, planned, exclude=exclude)
        via_radius = tva.diameter_mm / 2.0 * MM
        result = []
        skipped = 0
        for p in points:
            if self.cfg.skip_existing_components and self._via_already_exists(existing_vias, p, tva.net):
                skipped += 1
                continue
            free_p = find_free_point(
                p, keepout_excl, via_radius,
                preferred_direction=None,
                step_mm=self.cfg.via_search_step_mm,
                max_radius_mm=self.cfg.via_search_max_radius_mm,
                n_directions=self.cfg.via_search_n_directions,
            )
            if free_p is None:
                logger.warning(f"Термовиа: место для ({p.x/MM:.3f}, {p.y/MM:.3f}) мм не найдено, точка пропущена")
                continue
            result.append(ViaCommand(free_p, tva.drill_mm, tva.diameter_mm, tva.net, tva.target_ref))
        if skipped:
            logger.info(f"Пропущено {skipped} термовиа, уже существующих на плате")
        logger.info(f"Запланировано {len(result)} термовиа на {tva.pad}")
        return result
