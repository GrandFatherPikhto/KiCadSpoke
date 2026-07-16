# kicadspoke/placement/services/manual_position_calculator.py

import logging
from typing import List, Tuple
from kipy.board_types import FootprintInstance

from ...config import Config, Rule
from ...kicad.adapter import KiCadBoardAdapter
from ...geometry.spoke_layout import apply_spoke_geometry
from ..commands import PlacedComponentInfo, ViaCommand
from .component_pool import ComponentPool

logger = logging.getLogger(__name__)


class ManualPositionCalculator:
    """
    Ручное позиционирование компонентов и via по шаблонам спиц (см.
    geometry/spoke_layout.py). Геометрия зоны больше не нужна вообще —
    всё определяется pad + spoke.shift/rotation + содержимое шаблона.

    Конкретные ref компонентов НЕ читаются из конфига — подбираются из
    ComponentPool (по реальной цепи правила + пользовательскому полю
    Role), один пул на правило, разбираемый по очереди при обработке его
    спиц в порядке следования в YAML.

    ИЗМЕНЕНО (KiCadSpoke, обобщённые via): компоненты и via считаются в
    ОДНОМ проходе по спицам (пул потребляется один раз) — via больше не
    зависит от живой платы, поэтому возвращаются сразу как готовые
    ViaCommand, а не переносятся отдельным путём через via_planner.
    """

    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config

    def compute_raw_positions(
        self,
        target_fp: FootprintInstance,
        rules: List[Rule],
        side: str
    ) -> Tuple[List[PlacedComponentInfo], List[ViaCommand]]:
        components_result: List[PlacedComponentInfo] = []
        vias_result: List[ViaCommand] = []

        for rule in rules:
            # Собираем ВСЕ роли, нужные хоть одной спице этого правила --
            # пул строится один раз на всё правило, не на каждую спицу.
            roles_needed = set()
            for spoke in rule.spokes:
                if not spoke.enabled:
                    continue
                template = self.cfg.templates.get(spoke.template)
                if template is None:
                    continue
                roles_needed.update(slot.role for slot in template.components)

            pool = ComponentPool(self.adapter, rule.net, roles=sorted(roles_needed))

            for spoke in rule.spokes:
                if not spoke.enabled:
                    continue
                template = self.cfg.templates.get(spoke.template)
                if template is None:
                    logger.warning(f"Спица на паде {spoke.pad}: шаблон {spoke.template!r} "
                                   f"не найден в templates, спица пропущена")
                    continue

                pad = self.adapter.get_pad_by_number(target_fp, spoke.pad)
                if pad is None:
                    logger.warning(f"У {self.cfg.target_ref} нет площадки {spoke.pad}, "
                                   f"спица пропущена")
                    continue

                # Разбираем пул по ролям, нужным ИМЕННО этому шаблону --
                # ValidationError из pool.pop() фатально всплывёт наружу,
                # если на какую-то роль не хватило компонентов.
                role_to_ref = {slot.role: pool.pop(slot.role, spoke.pad) for slot in template.components}

                layout = apply_spoke_geometry(pad.position, spoke, template, rule.net, role_to_ref)

                # Via уровня спицы (была power_via)
                for via in layout.vias:
                    vias_result.append(ViaCommand(
                        position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                        net_name=via.net, owner_ref=self.cfg.target_ref
                    ))
                    logger.debug(f"  via спицы (пад {spoke.pad}): "
                                f"({via.position.x/1e6:.3f}, {via.position.y/1e6:.3f}) мм, net={via.net}")

                for comp_layout in layout.components:
                    components_result.append(PlacedComponentInfo(
                        ref=comp_layout.ref, dest=comp_layout.position, angle_deg=comp_layout.angle_deg,
                    ))
                    logger.debug(
                        f"  {comp_layout.ref} (роль {comp_layout.role}, пад {spoke.pad}): "
                        f"позиция ({comp_layout.position.x/1e6:.3f}, {comp_layout.position.y/1e6:.3f}) мм, "
                        f"угол {comp_layout.angle_deg:.1f}°"
                    )
                    # Via уровня компонента (была GND via)
                    for via in comp_layout.vias:
                        vias_result.append(ViaCommand(
                            position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                            net_name=via.net, owner_ref=comp_layout.ref
                        ))
                        logger.debug(f"    via {comp_layout.ref}: "
                                    f"({via.position.x/1e6:.3f}, {via.position.y/1e6:.3f}) мм, net={via.net}")

        return components_result, vias_result
