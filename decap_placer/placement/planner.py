# decap_placer/placement/planner.py

import math
import logging
from typing import List, Tuple, Optional
from kipy.board_types import BoardLayer, Pad
from kipy.geometry import Vector2, Angle

from ..config import Config, ViaConfig, Assignment
from ..kicad.adapter import KiCadBoardAdapter
from ..geometry.strategies import PlacementStrategy, RadialStrategy, OrthogonalStrategy, FixedStrategy
from ..geometry.boundary import polyline_points
from ..geometry.thermal_grid import compute_thermal_via_grid
from ..utils.units import MM
from ..exceptions import ComponentNotFoundError, GeometryError

from dataclasses import dataclass

logger = logging.getLogger(__name__)

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

class PlacementPlanner:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config
        self._strategy = self._create_strategy()
        self._target_fp = adapter.get_footprint(config.target_ref)
        if self._target_fp is None:
            raise ComponentNotFoundError(f"Целевой компонент {config.target_ref} не найден")
        self._center = self._target_fp.position
        self._target_layer = BoardLayer.BL_B_Cu if config.side == "back" else BoardLayer.BL_F_Cu
        self._boundary_polygon = self._get_boundary_polygon()
        logger.info(f"Планировщик инициализирован: target={config.target_ref}, side={config.side}")

    def _create_strategy(self) -> PlacementStrategy:
        mode = self.cfg.rotation_mode
        if mode == "radial":
            logger.debug("Выбрана радиальная стратегия")
            return RadialStrategy()
        elif mode == "orthogonal":
            logger.debug("Выбрана ортогональная стратегия")
            return OrthogonalStrategy()
        elif mode == "fixed":
            logger.debug(f"Выбрана фиксированная стратегия (угол {self.cfg.fixed_angle_deg}°)")
            return FixedStrategy()
        else:
            raise ValueError(f"Неизвестный rotation_mode: {mode}")

    def _get_boundary_polygon(self):
        zone = self.adapter.get_zone_by_name(self.cfg.boundary_zone)
        if zone is None:
            raise ComponentNotFoundError(f"Зона {self.cfg.boundary_zone} не найдена")
        pts = polyline_points(zone.outline.outline)
        logger.debug(f"Граница зоны содержит {len(pts)} точек")
        return pts

    def _find_pad(self, fp, pad_number: str) -> Optional[Pad]:
        for item in fp.definition.items:
            if isinstance(item, Pad) and item.number == pad_number:
                return item
        return None

    def _mirror_angle(self, angle_deg: float) -> float:
        if self.cfg.side == "back":
            return 180.0 - angle_deg
        return angle_deg

    def _merge_via_config(self, assignment: Assignment) -> ViaConfig:
        global_cfg = self.cfg.via
        override = assignment.via
        if override is None:
            return global_cfg
        if isinstance(override, bool):
            if override:
                return global_cfg
            else:
                return ViaConfig(enabled=False)
        if isinstance(override, ViaConfig):
            merged = ViaConfig(**global_cfg.__dict__)
            for key, value in override.__dict__.items():
                if value is not None:
                    setattr(merged, key, value)
            return merged
        raise ValueError(f"Некорректное значение via: {override}")

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

    def _plan_thermal_vias(self) -> List[ViaCommand]:
        tva = self.cfg.thermal_via_array
        if not tva.enabled:
            return []
        logger.debug(f"Планирование термовиа для {tva.target_ref}, площадка {tva.pad}")
        fp = self.adapter.get_footprint(tva.target_ref)
        if fp is None:
            raise ComponentNotFoundError(f"Термопад: компонент {tva.target_ref} не найден")
        pad = self._find_pad(fp, tva.pad)
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
        logger.info(f"Запланировано {len(points)} термовиа на {tva.pad}")
        return [ViaCommand(p, tva.drill_mm, tva.diameter_mm, tva.net, tva.target_ref) for p in points]

    def plan(self) -> Tuple[List[MoveCommand], List[ViaCommand]]:
        moves = []
        vias = []

        logger.info("Начало планирования по правилам")
        for rule_idx, rule in enumerate(self.cfg.rules):
            logger.debug(f"Обработка цепи {rule.net} ({rule_idx+1}/{len(self.cfg.rules)})")
            net = self.adapter.get_net_by_name(rule.net)
            if net is None:
                logger.warning(f"Цепь {rule.net} не найдена, пропускаем")
                continue
            for ass_idx, assignment in enumerate(rule.assignments):
                pad = self._find_pad(self._target_fp, assignment.pad)
                if pad is None:
                    logger.warning(f"У {self.cfg.target_ref} нет площадки {assignment.pad}, пропуск")
                    continue

                try:
                    dest, direction = self._strategy.compute_position(
                        self._center,
                        pad.position,
                        self._boundary_polygon,
                        assignment.placement,
                        assignment.offset_mm,
                        fixed_angle_deg=self.cfg.fixed_angle_deg
                    )
                except GeometryError as e:
                    raise GeometryError(f"Ошибка для {assignment.ref}: {e}")

                phi_deg = math.degrees(math.atan2(direction[1], direction[0]))
                phi_deg = self._mirror_angle(phi_deg)
                angle = Angle.from_degrees(phi_deg)

                moves.append(MoveCommand(
                    ref=assignment.ref,
                    position=dest,
                    angle=angle,
                    layer=self._target_layer
                ))
                logger.debug(f"  {assignment.ref} -> ({dest.x/MM:.3f}, {dest.y/MM:.3f}) мм, угол={phi_deg:.1f}°")

                via_cfg = self._merge_via_config(assignment)
                if via_cfg.enabled:
                    via_net = self.adapter.get_net_by_name(via_cfg.net)
                    if via_net is None:
                        logger.warning(f"Цепь {via_cfg.net} для виа у {assignment.ref} не найдена")
                        continue
                    via_positions = self._plan_stitching_vias(dest, direction, via_cfg, assignment.placement)
                    for pos in via_positions:
                        vias.append(ViaCommand(
                            position=pos,
                            drill_mm=via_cfg.drill_mm,
                            diameter_mm=via_cfg.diameter_mm,
                            net_name=via_cfg.net,
                            owner_ref=assignment.ref
                        ))
                        logger.debug(f"    виа у {assignment.ref}: ({pos.x/MM:.3f}, {pos.y/MM:.3f}) мм")

        # Термовиа
        vias.extend(self._plan_thermal_vias())

        logger.info(f"Планирование завершено: {len(moves)} перемещений, {len(vias)} виа")
        return moves, vias