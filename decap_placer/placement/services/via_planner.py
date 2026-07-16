# decap_placer/placement/services/via_planner.py

import logging
from typing import List, Optional, Set, Tuple
from kipy.board_types import FootprintInstance, BoardLayer
from kipy.geometry import Vector2

from ...config import Config, Rule
from ...geometry.keepout import Rect, build_keepout, find_free_point
from ...geometry.thermal_grid import compute_thermal_via_grid
from ...geometry.spoke_layout import apply_spoke_geometry, rotate_local_offset
from ...kicad.adapter import KiCadBoardAdapter
from ...utils.units import MM
from ...exceptions import GeometryError, ComponentNotFoundError
from ..commands import ViaCommand, PlacedComponentInfo

logger = logging.getLogger(__name__)


class ViaPlanner:
    """
    Планирование всех виа: power via (по шаблону спицы), GND via (по
    шаблону, от РЕАЛЬНОГО пада уже размещённого компонента) и термовиа.

    Геометрия зоны (boundary_polygon, zone_center) больше не нужна нигде —
    вся ручная расстановка полностью самодостаточна: пад FPGA + шаблон +
    сдвиг/поворот спицы. plan_vias() должен вызываться ПОСЛЕ того, как
    планировщик уже закоммитил перемещения компонентов (см. executor) —
    GND via ищет реальный пад на уже перемещённом футпринте, а не
    предсказывает его позицию заранее.
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
        planned: List[PlacedComponentInfo],
        target_fp: FootprintInstance,
        rules: List[Rule],
        target_layer: BoardLayer
    ) -> List[ViaCommand]:
        keepout = self._build_keepout(target_fp, planned)
        logger.debug(f"Keepout: {len(keepout)} прямоугольников")

        existing_vias = self.adapter.get_vias() if self.cfg.skip_existing_components else []

        power = self._plan_power_vias(target_fp, rules, existing_vias)
        gnd = self._plan_gnd_vias(planned, existing_vias)
        thermal = self._plan_thermal_vias(planned, target_fp, keepout, existing_vias)

        vias = []
        vias.extend(power)
        vias.extend(gnd)
        vias.extend(thermal)

        logger.info(f"plan_vias завершено: {len(vias)} виа")
        return vias

    def _plan_power_vias(self, target_fp: FootprintInstance, rules: List[Rule],
                        existing_vias: Optional[List] = None) -> List[ViaCommand]:
        """Power via — целиком из шаблона спицы (pad + shift/rotation + template.power_via)."""
        existing_vias = existing_vias or []
        result = []
        skipped = 0
        for rule in rules:
            for spoke in rule.spokes:
                if not spoke.enabled:
                    continue
                template = self.cfg.templates.get(spoke.template)
                if template is None or template.power_via is None:
                    continue

                pad = self.adapter.get_pad_by_number(target_fp, spoke.pad)
                if pad is None:
                    logger.warning(f"Power via: у {self.cfg.target_ref} нет площадки {spoke.pad}")
                    continue

                layout = apply_spoke_geometry(pad.position, spoke, template, rule.net)
                if layout.power_via_pos is None:
                    continue

                net = self.adapter.get_net_by_name(layout.power_via_net)
                if net is None:
                    logger.warning(f"Power via для {spoke.pad}: цепь {layout.power_via_net} не найдена")
                    continue

                if self.cfg.skip_existing_components and self._via_already_exists(
                        existing_vias, layout.power_via_pos, layout.power_via_net):
                    skipped += 1
                    logger.debug(f"  power via для {spoke.pad}: уже существует, пропущена")
                    continue

                result.append(ViaCommand(
                    position=layout.power_via_pos,
                    drill_mm=layout.power_via_drill_mm,
                    diameter_mm=layout.power_via_diameter_mm,
                    net_name=layout.power_via_net,
                    owner_ref=self.cfg.target_ref
                ))
                logger.debug(f"  power via для {spoke.pad}: "
                            f"({layout.power_via_pos.x/MM:.3f}, {layout.power_via_pos.y/MM:.3f}) мм")
        if skipped:
            logger.info(f"Пропущено {skipped} power via, уже существующих на плате")
        return result

    def _plan_gnd_vias(self, planned: List[PlacedComponentInfo],
                       existing_vias: Optional[List] = None) -> List[ViaCommand]:
        """
        GND via — от РЕАЛЬНОГО пада уже размещённого компонента (не
        предсказание "где он окажется", а факт "где он оказался") плюс
        локальное смещение из шаблона, повёрнутое на угол спицы.
        """
        existing_vias = existing_vias or []
        result = []
        skipped = 0
        for info in planned:
            fp = self.adapter.get_footprint(info.ref)
            if fp is None:
                logger.warning(f"{info.ref}: компонент не найден после перемещения, GND via пропущена")
                continue

            gnd_pad = None
            for pad in self.adapter.get_footprint_pads(fp):
                if pad.net and pad.net.name == info.gnd_via_net:
                    gnd_pad = pad
                    break
            if gnd_pad is None:
                logger.warning(f"{info.ref}: нет пада с цепью {info.gnd_via_net!r}, GND via пропущена")
                continue

            net = self.adapter.get_net_by_name(info.gnd_via_net)
            if net is None:
                logger.warning(f"{info.ref}: цепь {info.gnd_via_net} не найдена, GND via пропущена")
                continue

            offset = rotate_local_offset(
                info.gnd_via_offset_along_mm, info.gnd_via_offset_across_mm, info.rotation_deg
            )
            pos = Vector2.from_xy(gnd_pad.position.x + offset.x, gnd_pad.position.y + offset.y)

            if self.cfg.skip_existing_components and self._via_already_exists(existing_vias, pos, info.gnd_via_net):
                skipped += 1
                logger.debug(f"  GND via для {info.ref}: уже существует, пропущена")
                continue

            result.append(ViaCommand(
                position=pos,
                drill_mm=info.gnd_via_drill_mm,
                diameter_mm=info.gnd_via_diameter_mm,
                net_name=info.gnd_via_net,
                owner_ref=info.ref
            ))
            logger.debug(f"  GND via для {info.ref}: ({pos.x/MM:.3f}, {pos.y/MM:.3f}) мм")
        if skipped:
            logger.info(f"Пропущено {skipped} GND via, уже существующих на плате")
        return result

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
            # Раньше здесь искали направление "к центру зоны" — зоны
            # больше нет, ищем просто ближайшее свободное место по кругу.
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
