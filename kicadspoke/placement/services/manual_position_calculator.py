# kicadspoke/placement/services/manual_position_calculator.py

import logging
from typing import List, Tuple
from kipy.board_types import FootprintInstance, BoardLayer

from ...config import Config, Rule
from ...kicad.adapter import KiCadBoardAdapter
from ...geometry.spoke_layout import apply_spoke_geometry
from ..commands import PlacedComponentInfo, ViaCommand, TrackCommand
from ...registry import make_registry_key
from .component_pool import ComponentPool
from ..interfaces import IPositionCalculator


logger = logging.getLogger(__name__)


class ManualPositionCalculator(IPositionCalculator):
    """
    Ручное позиционирование компонентов и via по шаблонам спиц.
    Поддерживает кластеры: для каждого уникального кластера в правиле
    строится отдельный ComponentPool, и спицы берут компоненты из своего
    кластера.
    """

    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config

    def compute_raw_positions(
        self,
        rules: List[Rule],
    ) -> Tuple[List[PlacedComponentInfo], List[ViaCommand], List[TrackCommand]]:
        components_result: List[PlacedComponentInfo] = []
        vias_result: List[ViaCommand] = []
        tracks_result: List[TrackCommand] = []

        for rule in rules:
            # --- Резолвим якорь (anchor_ref или anchor_role) ---
            if rule.anchor_ref is not None:
                target_fp = self.adapter.get_footprint(rule.anchor_ref)
                if target_fp is None:
                    from ...exceptions import ComponentNotFoundError
                    raise ComponentNotFoundError(
                        f"правило (цепь {rule.net!r}): якорь {rule.anchor_ref!r} не найден на плате")
            else:
                from .clone_role_resolver import resolve_footprint_by_role
                target_fp = resolve_footprint_by_role(
                    self.adapter,
                    rule.anchor_role,
                    rule.anchor_sheet,
                    rule.anchor_cluster,
                    self.cfg.sheet_names,
                    label=f"правило (цепь {rule.net!r})",
                )
            anchor_ref_resolved = target_fp.reference_field.text.value

            # --- Собираем все роли, нужные для этого правила ---
            roles_needed = set()
            for spoke in rule.spokes:
                if not spoke.enabled:
                    continue
                template = self.cfg.templates.get(spoke.template)
                if template is not None:
                    roles_needed.update(slot.role for slot in template.components)

            # ВАЖНО: не пропускаем правило целиком, если roles_needed пуст —
            # это значит только "ни у одного шаблона спицы нет роле-
            # компонентов", а не "у правила нет спиц вообще". Спицы могут
            # нести via уровня самой спицы без единого под-компонента (см.
            # cap_pair_standard без components: в старых конфигах) — им
            # пул вообще не нужен, но геометрию/via создавать всё равно
            # надо. Пустой roles_needed просто даёт пустые пулы ниже —
            # дёшево, не требует отдельной ветки.

            # --- Собираем кластеры, которые используются в спицах (включая None) ---
            clusters_needed = {spoke.cluster for spoke in rule.spokes if spoke.enabled}

            # --- Строим пулы для каждого кластера, включая None (спицы без
            # кластера) — clusters_needed уже содержит None, если хоть одна
            # включённая спица без кластера, так что отдельный проход не нужен ---
            pools_by_cluster = {}
            for cluster in clusters_needed:
                pools_by_cluster[cluster] = ComponentPool(
                    self.adapter,
                    rule.net,
                    roles=sorted(roles_needed),
                    cluster=cluster
                )

            # --- Обрабатываем каждую спицу ---
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
                    logger.warning(f"У {anchor_ref_resolved} нет площадки {spoke.pad}, "
                                   f"спица пропущена")
                    continue

                # Выбираем пул по кластеру спицы — по построению pools_by_cluster
                # уже содержит ключ spoke.cluster (см. clusters_needed выше),
                # для любой включённой спицы; если это когда-нибудь перестанет
                # быть так — пусть падает громко (KeyError), а не тихо
                # подставляет свежесозданный пул мимо общего учёта расхода.
                pool = pools_by_cluster[spoke.cluster]

                # Разбираем пул по ролям
                role_to_ref = {slot.role: pool.pop(slot.role, spoke.pad) for slot in template.components}

                layout = apply_spoke_geometry(pad.position, spoke, template, rule.net, role_to_ref)
                anchor_id = f"pad:{spoke.pad}"

                # Via уровня спицы
                for via_index, via in enumerate(layout.vias):
                    vias_result.append(ViaCommand(
                        position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                        net_name=via.net, owner_ref=anchor_ref_resolved,
                        registry_key=make_registry_key(anchor_id, spoke.template, None, via_index),
                    ))
                    logger.debug(f"  via спицы (пад {spoke.pad}): "
                                f"({via.position.x/1e6:.3f}, {via.position.y/1e6:.3f}) мм, net={via.net}")

                # Треки уровня спицы (net=null в шаблоне наследует rule.net —
                # см. spoke_layout._resolve_track). Только уровень спицы:
                # TemplateComponentSlot треков не несёт, только via.
                for track_index, track in enumerate(layout.tracks):
                    track_layer = BoardLayer.BL_B_Cu if track.layer == 'B.Cu' else BoardLayer.BL_F_Cu
                    tracks_result.append(TrackCommand(
                        start=track.start, end=track.end, width_mm=track.width_mm,
                        net_name=track.net, layer=track_layer, owner_ref=anchor_ref_resolved,
                        registry_key=make_registry_key(anchor_id, spoke.template, None, track_index),
                    ))
                    logger.debug(f"  track спицы (пад {spoke.pad}): "
                                f"({track.start.x/1e6:.3f}, {track.start.y/1e6:.3f}) -> "
                                f"({track.end.x/1e6:.3f}, {track.end.y/1e6:.3f}) мм, "
                                f"net={track.net}, layer={track.layer}")

                for comp_layout in layout.components:
                    components_result.append(PlacedComponentInfo(
                        ref=comp_layout.ref, dest=comp_layout.position, angle_deg=comp_layout.angle_deg,
                    ))
                    logger.debug(
                        f"  {comp_layout.ref} (роль {comp_layout.role}, пад {spoke.pad}): "
                        f"позиция ({comp_layout.position.x/1e6:.3f}, {comp_layout.position.y/1e6:.3f}) мм, "
                        f"угол {comp_layout.angle_deg:.1f}°"
                    )
                    for via_index, via in enumerate(comp_layout.vias):
                        vias_result.append(ViaCommand(
                            position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                            net_name=via.net, owner_ref=comp_layout.ref,
                            registry_key=make_registry_key(anchor_id, spoke.template, comp_layout.role, via_index),
                        ))
                        logger.debug(f"    via {comp_layout.ref}: "
                                    f"({via.position.x/1e6:.3f}, {via.position.y/1e6:.3f}) мм, net={via.net}")

        return components_result, vias_result, tracks_result