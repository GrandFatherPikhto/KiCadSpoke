# decap_placer/placement/services/via_planner.py
import math
import logging
from typing import List, Tuple, Optional, Set
from kipy.board_types import FootprintInstance, Pad
from kipy.geometry import Vector2

from ...config import Config, ViaConfig, SpokeComponent
from ...geometry.keepout import Rect, build_keepout, find_free_point
from ...geometry.thermal_grid import compute_thermal_via_grid
from ...kicad.adapter import KiCadBoardAdapter
from ...utils.units import MM
from ...exceptions import GeometryError, ComponentNotFoundError
from ..commands import ViaCommand
from .keepout_builder import KeepoutBuilder

logger = logging.getLogger(__name__)

class ViaPlanner:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config
        self.keepout_builder = KeepoutBuilder(adapter, config)

    def plan_vias(
        self,
        planned: List[Tuple[SpokeComponent, Vector2, Tuple[float, float]]],
        target_fp: FootprintInstance,
        zone_center_point: Vector2,
        boundary_polygon: List[Vector2]
    ) -> List[ViaCommand]:
        # 1. Keepout
        keepout = self.keepout_builder.build_keepout(
            target_fp=target_fp,
            cap_refs={component.ref for component, _, _ in planned}
        )
        logger.debug(f"Keepout для stitching-виа: {len(keepout)} прямоугольников "
                     f"(клиренс {self.cfg.via_keepout_clearance_mm}мм)")

        vias = []
        # 2. Stitching via
        for component, new_pos, direction in planned:
            via_cfg = self._merge_via_config(component)
            if not via_cfg.enabled:
                continue
            via_net = self.adapter.get_net_by_name(via_cfg.net)
            if via_net is None:
                logger.warning(f"Цепь {via_cfg.net} для виа у {component.ref} не найдена")
                continue

            via_positions = self._plan_stitching_vias(new_pos, direction, via_cfg, component.placement)
            via_radius = via_cfg.diameter_mm / 2.0 * MM

            for pos in via_positions:
                preferred = self._zone_preferred_direction(pos, via_cfg.net, zone_center_point)
                free_pos = find_free_point(
                    pos, keepout, via_radius,
                    preferred_direction=preferred,
                    step_mm=self.cfg.via_search_step_mm,
                    max_radius_mm=self.cfg.via_search_max_radius_mm,
                    n_directions=self.cfg.via_search_n_directions,
                )
                if free_pos is None:
                    logger.warning(f"    виа у {component.ref}: свободное место не найдено "
                                   f"(клиренс {self.cfg.via_keepout_clearance_mm}мм), виа пропущена")
                    continue
                vias.append(ViaCommand(
                    position=free_pos,
                    drill_mm=via_cfg.drill_mm,
                    diameter_mm=via_cfg.diameter_mm,
                    net_name=via_cfg.net,
                    owner_ref=component.ref
                ))
                moved = " [сдвинута для обхода keepout]" if (free_pos.x, free_pos.y) != (pos.x, pos.y) else ""
                logger.debug(f"    виа у {component.ref}: ({free_pos.x/MM:.3f}, {free_pos.y/MM:.3f}) мм{moved}")

        # 3. Термовиа
        vias.extend(self._plan_thermal_vias(planned, target_fp, zone_center_point))
        logger.info(f"plan_vias завершено: {len(vias)} виа")
        return vias

    def _plan_thermal_vias(self, planned, target_fp, zone_center_point) -> List[ViaCommand]:
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
        net = self.adapter.get_net_by_name(tva.net)
        if net is None:
            raise ComponentNotFoundError(f"Термопад: цепь {tva.net} не найдена")
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

        keepout = self.keepout_builder.build_keepout(
            target_fp=target_fp,
            cap_refs={component.ref for component, _, _ in planned},
            exclude={(tva.target_ref, tva.pad)}
        )
        via_radius = tva.diameter_mm / 2.0 * MM
        result = []
        skipped = 0
        for p in points:
            preferred = self._zone_preferred_direction(p, tva.net, zone_center_point)
            free_p = find_free_point(
                p, keepout, via_radius,
                preferred_direction=preferred,
                step_mm=self.cfg.via_search_step_mm,
                max_radius_mm=self.cfg.via_search_max_radius_mm,
                n_directions=self.cfg.via_search_n_directions,
            )
            if free_p is None:
                skipped += 1
                logger.warning(f"Термовиа: свободное место для точки "
                               f"({p.x/MM:.3f}, {p.y/MM:.3f}) мм не найдено, точка пропущена")
                continue
            result.append(ViaCommand(free_p, tva.drill_mm, tva.diameter_mm, tva.net, tva.target_ref))
        logger.info(f"Запланировано {len(result)} термовиа на {tva.pad}"
                    + (f" ({skipped} пропущено, места не нашлось)" if skipped else ""))
        return result

    def _merge_via_config(self, component: SpokeComponent) -> ViaConfig:
        global_dict = dict(self.cfg.via.__dict__)
        override = component.via
        if override is None:
            return ViaConfig(**global_dict)
        if isinstance(override, bool):
            if override:
                return ViaConfig(**global_dict)
            else:
                merged = dict(global_dict)
                merged["enabled"] = False
                return ViaConfig(**merged)
        if isinstance(override, dict):
            merged = dict(global_dict)
            merged.update(override)
            return ViaConfig(**merged)
        raise ValueError(f"Некорректное значение via: {override!r}")

    def _zone_preferred_direction(self, ideal: Vector2, net_name: str, zone_center: Vector2) -> Optional[Tuple[float, float]]:
        if net_name.upper() != "GND":
            return None
        dx = zone_center.x - ideal.x
        dy = zone_center.y - ideal.y
        length = math.hypot(dx, dy)
        if length == 0:
            return None
        return (dx / length, dy / length)

    def _get_pad_for_net(self, fp: FootprintInstance, net_name: str) -> Optional[Tuple[Vector2, float]]:
        pads = self.adapter.get_footprint_pads(fp)
        for pad in pads:
            if pad.net.name == net_name:
                bbox = self.adapter.get_bounding_boxes([pad])[0]
                if bbox is not None:
                    radius = math.hypot(bbox.size.x, bbox.size.y) / 2.0
                else:
                    radius = 0.3 * MM
                return pad.position, radius
        return None

    def _compute_via_positions_from_pad(self, pad_center: Vector2, pad_radius: float,
                                         direction: Tuple[float, float], away_sign: float,
                                         offset_mm: float, count: int) -> List[Vector2]:
        ux, uy = direction
        gap = 0.05 * MM
        offset = offset_mm * MM

        start = pad_center + Vector2.from_xy(
            int(ux * (pad_radius + gap)),
            int(uy * (pad_radius + gap))
        )
        offset_vec = Vector2.from_xy(
            int(ux * offset * away_sign),
            int(uy * offset * away_sign)
        )
        base_point = start + offset_vec

        if count == 1:
            return [base_point]
        elif count == 2:
            px, py = -uy, ux
            return [
                base_point + Vector2.from_xy(int(px * offset), int(py * offset)),
                base_point - Vector2.from_xy(int(px * offset), int(py * offset)),
            ]
        else:
            raise ValueError(f"via.count поддерживает 1 или 2, получено {count}")

    def _plan_stitching_vias(self, cap_point: Vector2, direction: Tuple[float, float],
                            via_cfg: ViaConfig, placement: str) -> List[Vector2]:
        ux, uy = direction
        away_sign = -1.0 if placement == "inside" else 1.0
        offset = via_cfg.offset_from_cap_mm * MM
        count = via_cfg.count

        if count == 1:
            mode = via_cfg.direction
            if mode == "away_from_pad":
                vx, vy = ux * away_sign, uy * away_sign
            elif mode == "toward_pad":
                vx, vy = -ux * away_sign, -uy * away_sign
            elif mode == "perpendicular":
                vx, vy = -uy, ux
            else:
                raise ValueError(f"неизвестный via.direction: {mode}")
            return [Vector2.from_xy(int(cap_point.x + vx * offset), int(cap_point.y + vy * offset))]
        elif count == 2:
            px, py = -uy, ux
            return [
                Vector2.from_xy(int(cap_point.x + px * offset), int(cap_point.y + py * offset)),
                Vector2.from_xy(int(cap_point.x - px * offset), int(cap_point.y - py * offset)),
            ]
        else:
            raise ValueError(f"via.count поддерживает 1 или 2, получено {count}")