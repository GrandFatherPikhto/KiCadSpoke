# kicadspoke/placement/services/clone_position_calculator.py
"""
clone_position_calculator.py — аналог manual_position_calculator.py, но
для ClonePlacement (TemplatePlacer). Не реализует IPositionCalculator —
тот интерфейс сделан специально под pad-привязанную модель ManualSpoke
(target_fp/rules/side); ClonePlacement работает принципиально иначе —
якорь это просто имя (anchor_id = f"name:{clone.name}"), не номер пада
какого-то одного целевого компонента.
"""
import logging
from typing import List, Tuple, Optional
from kipy.geometry import Vector2
from ...config import Config, ClonePlacement
from ...exceptions import ValidationError, format_fatal_error
from ...kicad.adapter import KiCadBoardAdapter
from ...geometry.clone_geometry import apply_clone_geometry
from ...registry import make_registry_key
from ..commands import PlacedComponentInfo, ViaCommand
from .clone_role_resolver import resolve_roles_by_selection, resolve_roles_by_nets, clone_uses_selection_mode

logger = logging.getLogger(__name__)


class ClonePositionCalculator:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config

    def _resolve_anchor(self, clone: ClonePlacement) -> Optional[Vector2]:
        """
        anchor_ref/anchor_pad -> абсолютная точка якоря. None, если якорь
        не задан (режим абсолютных координат). Несуществующий ref/pad —
        ФАТАЛЬНО: якорь задан явно, разместить секцию «где-то» или молча
        пропустить — оба варианта хуже падения.
        """
        if clone.anchor_ref is None:
            return None
        fp = self.adapter.get_footprint(clone.anchor_ref)
        if fp is None:
            raise ValidationError(format_fatal_error(
                f"{clone.name}: якорный компонент {clone.anchor_ref!r} не найден на плате",
                [f"проверь anchor_ref в clone_placement {clone.name!r} — "
                 f"такого ref на плате нет (опечатка? компонент ещё не в PCB?)"]
            ))
        if clone.anchor_pad is None:
            logger.debug(f"  [{clone.name}] якорь: центр {clone.anchor_ref} "
                         f"({fp.position.x/1e6:.3f}, {fp.position.y/1e6:.3f}) мм")
            return fp.position
        pad = self.adapter.get_pad_by_number(fp, clone.anchor_pad)
        if pad is None:
            raise ValidationError(format_fatal_error(
                f"{clone.name}: у {clone.anchor_ref} нет площадки {clone.anchor_pad!r}",
                [f"проверь anchor_pad в clone_placement {clone.name!r} — "
                 f"номера падов это строки как в KiCad ('1', '17', 'A3')"]
            ))
        logger.debug(f"  [{clone.name}] якорь: пад {clone.anchor_ref}.{clone.anchor_pad} "
                     f"({pad.position.x/1e6:.3f}, {pad.position.y/1e6:.3f}) мм")
        return pad.position

    def compute_raw_positions(
        self,
        clone_placements: List[ClonePlacement],
    ) -> Tuple[List[PlacedComponentInfo], List[ViaCommand]]:
        components_result: List[PlacedComponentInfo] = []
        vias_result: List[ViaCommand] = []

        for clone in clone_placements:
            if not clone.enabled:
                continue
            template = self.cfg.templates.get(clone.template)
            if template is None:
                logger.warning(f"{clone.name}: шаблон {clone.template!r} не найден в templates, пропуск")
                continue

            # Режим "по цепям", если заданы nets ИЛИ params -- иначе "по
            # выделению". Явное решение снаружи, не автоматика внутри
            # самого резолвера (см. clone_role_resolver.py).
            if clone_uses_selection_mode(clone):
                role_to_ref = resolve_roles_by_selection(self.adapter, template, clone.name)
            else:
                role_to_ref = resolve_roles_by_nets(self.adapter, template, clone)

            layout = apply_clone_geometry(clone, template, role_to_ref,
                                          anchor_position=self._resolve_anchor(clone))
            anchor_id = f"name:{clone.name}"

            for via_index, via in enumerate(layout.vias):
                vias_result.append(ViaCommand(
                    position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                    net_name=via.net, owner_ref=clone.name,
                    registry_key=make_registry_key(anchor_id, clone.template, None, via_index),
                ))
                logger.debug(f"  [{clone.name}] via спицы: "
                            f"({via.position.x/1e6:.3f}, {via.position.y/1e6:.3f}) мм, net={via.net}")

            for comp_layout in layout.components:
                components_result.append(PlacedComponentInfo(
                    ref=comp_layout.ref, dest=comp_layout.position, angle_deg=comp_layout.angle_deg,
                ))
                logger.debug(
                    f"  [{clone.name}] {comp_layout.ref} (роль {comp_layout.role}): "
                    f"позиция ({comp_layout.position.x/1e6:.3f}, {comp_layout.position.y/1e6:.3f}) мм, "
                    f"угол {comp_layout.angle_deg:.1f}°"
                )
                for via_index, via in enumerate(comp_layout.vias):
                    vias_result.append(ViaCommand(
                        position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                        net_name=via.net, owner_ref=comp_layout.ref,
                        registry_key=make_registry_key(anchor_id, clone.template, comp_layout.role, via_index),
                    ))

        return components_result, vias_result
