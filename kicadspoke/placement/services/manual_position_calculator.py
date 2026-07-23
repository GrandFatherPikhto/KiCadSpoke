# kicadspoke/placement/services/clone_position_calculator.py
"""
clone_position_calculator.py — аналог manual_position_calculator.py, но
для ClonePlacement (TemplatePlacer). Не реализует IPositionCalculator —
тот интерфейс сделан специально под pad-привязанную модель ManualSpoke
(target_fp/rules/side); ClonePlacement работает принципиально иначе.

anchor_id для реестра (см. registry.py) строится из ФИЗИЧЕСКОЙ привязки
(anchor_ref/anchor_pad), а не из clone.name — по той же причине, по
которой refdes не годится ключом канала: имя произвольно и меняется
(переименование clone_placement не должно тереть via/треки, если якорь
физически тот же). Только если anchor_ref вообще не задан (режим
абсолютных координат, редкий случай) — деваться некуда, используем
clone.name, единственный доступный идентификатор в этом режиме.
"""
import logging
from typing import List, Tuple, Optional
from kipy.geometry import Vector2
from kipy.board_types import BoardLayer
from ...config import Config, ClonePlacement
from ...exceptions import ValidationError, format_fatal_error
from ...kicad.adapter import KiCadBoardAdapter
from ...geometry.clone_geometry import apply_clone_geometry
from ...registry import make_registry_key
from ..commands import PlacedComponentInfo, ViaCommand, TrackCommand
from .clone_role_resolver import (resolve_roles_by_selection, resolve_roles_by_nets,
                                  clone_uses_selection_mode, resolve_anchor_by_role)

logger = logging.getLogger(__name__)


def clone_anchor_id(clone: ClonePlacement) -> str:
    """
    Идентичность clone_placement для реестра — физическая привязка, не
    имя. Порядок приоритета совпадает с порядком резолва якоря:
      anchor_ref задан -> "anchor:{ref}:{pad}"
      anchor_role задан -> "role:{anchor_role}:{anchor_sheet}:{pad}"
        (anchor_role — тоже устойчив к переименованию/перенумерации,
        как и anchor_ref к переименованию clone.name; anchor_sheet
        включён в ключ, потому что это часть УСЛОВИЙ поиска якоря —
        смена anchor_sheet тоже меняет физическое размещение)
      ни то, ни другое (абсолютные координаты) -> "name:{clone.name}",
        единственный доступный идентификатор в этом режиме.
    """
    if clone.anchor_ref is not None:
        return f"anchor:{clone.anchor_ref}:{clone.anchor_pad or ''}"
    if clone.anchor_role is not None:
        return f"role:{clone.anchor_role}:{clone.anchor_sheet or ''}:{clone.anchor_pad or ''}"
    return f"name:{clone.name}"


class ClonePositionCalculator:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config

    def _resolve_anchor(self, clone: ClonePlacement) -> Optional[Vector2]:
        """
        anchor_ref/anchor_pad ИЛИ anchor_role(+anchor_sheet)/anchor_pad ->
        абсолютная точка якоря. None, если якорь не задан (режим абсолютных
        координат). Несуществующий/неоднозначный якорь — ФАТАЛЬНО: якорь
        задан явно, разместить секцию «где-то» или молча пропустить — оба
        варианта хуже падения.
        """
        if clone.anchor_ref is not None:
            fp = self.adapter.get_footprint(clone.anchor_ref)
            if fp is None:
                raise ValidationError(format_fatal_error(
                    f"{clone.name}: якорный компонент {clone.anchor_ref!r} не найден на плате",
                    [f"проверь anchor_ref в clone_placement {clone.name!r} — "
                     f"такого ref на плате нет (опечатка? компонент ещё не в PCB?)"]
                ))
        elif clone.anchor_role is not None:
            fp = resolve_anchor_by_role(self.adapter, clone)
        else:
            return None

        if clone.anchor_pad is None:
            logger.debug(f"  [{clone.name}] якорь: центр {fp.reference_field.text.value} "
                         f"({fp.position.x/1e6:.3f}, {fp.position.y/1e6:.3f}) мм")
            return fp.position
        pad = self.adapter.get_pad_by_number(fp, clone.anchor_pad)
        if pad is None:
            raise ValidationError(format_fatal_error(
                f"{clone.name}: у {fp.reference_field.text.value} нет площадки {clone.anchor_pad!r}",
                [f"проверь anchor_pad в clone_placement {clone.name!r} — "
                 f"номера падов это строки как в KiCad ('1', '17', 'A3')"]
            ))
        logger.debug(f"  [{clone.name}] якорь: пад {fp.reference_field.text.value}.{clone.anchor_pad} "
                     f"({pad.position.x/1e6:.3f}, {pad.position.y/1e6:.3f}) мм")
        return pad.position

    def compute_raw_positions(
        self,
        clone_placements: List[ClonePlacement],
    ) -> Tuple[List[PlacedComponentInfo], List[ViaCommand], List[TrackCommand]]:
        components_result: List[PlacedComponentInfo] = []
        vias_result: List[ViaCommand] = []
        tracks_result: List[TrackCommand] = []

        for clone in clone_placements:
            if not clone.enabled:
                continue
            template = self.cfg.templates.get(clone.template)
            if template is None:
                logger.warning(f"{clone.name}: шаблон {clone.template!r} не найден в templates, пропуск")
                continue

            # Якорь считаем ДО резолва ролей — нужен для сужения физической
            # близостью (resolve_roles_by_nets), и тот же самый потом идёт
            # в apply_clone_geometry (не считаем дважды).
            anchor_position = self._resolve_anchor(clone)

            # Режим "по цепям", если заданы nets ИЛИ params -- иначе "по
            # выделению". Явное решение снаружи, не автоматика внутри
            # самого резолвера (см. clone_role_resolver.py).
            if clone_uses_selection_mode(clone):
                role_to_ref = resolve_roles_by_selection(self.adapter, template, clone.name)
            else:
                role_to_ref = resolve_roles_by_nets(self.adapter, template, clone,
                                                    anchor_position=anchor_position)

            # Сторона размещения: своя у клона или глобальная из конфига.
            # mirror — явная ручная операция; корректность пары layer/mirror
            # уже проверена фаталом в load_config.
            mirror = clone.mirror
            # Шаблон снят с front; back = зеркало (см. apply_clone_geometry)
            layout = apply_clone_geometry(clone, template, role_to_ref,
                                          anchor_position=anchor_position,
                                          mirror=mirror)
            logger.info(f"  [{clone.name}] шаблон {template.name!r} на {template.layer}"
                        + (" -> mirror: перевёрнут целиком" if mirror else " -> как записан"))
            anchor_id = clone_anchor_id(clone)

            for via_index, via in enumerate(layout.vias):
                vias_result.append(ViaCommand(
                    position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                    net_name=via.net, owner_ref=clone.name,
                    registry_key=make_registry_key(anchor_id, clone.template, None, via_index),
                ))
                logger.debug(f"  [{clone.name}] via спицы: "
                            f"({via.position.x/1e6:.3f}, {via.position.y/1e6:.3f}) мм, net={via.net}")

            for track_index, track in enumerate(layout.tracks):
                track_layer = BoardLayer.BL_B_Cu if track.layer == 'B.Cu' else BoardLayer.BL_F_Cu
                tracks_result.append(TrackCommand(
                    start=track.start, end=track.end, width_mm=track.width_mm,
                    net_name=track.net, layer=track_layer, owner_ref=clone.name,
                    registry_key=make_registry_key(anchor_id, clone.template, None, track_index),
                ))
                logger.debug(f"  [{clone.name}] track: "
                            f"({track.start.x/1e6:.3f}, {track.start.y/1e6:.3f}) -> "
                            f"({track.end.x/1e6:.3f}, {track.end.y/1e6:.3f}) мм, "
                            f"net={track.net}, layer={track.layer}")

            for comp_layout in layout.components:
                # Слой слота: свой абсолютный или наследованный от шаблона;
                # mirror инвертирует ВСЕ слои — конструкция переворачивается
                # целиком как физический объект.
                slot_layer = comp_layout.slot_layer or template.layer
                if mirror:
                    slot_layer = 'F.Cu' if slot_layer == 'B.Cu' else 'B.Cu'
                comp_layer = BoardLayer.BL_B_Cu if slot_layer == 'B.Cu' else BoardLayer.BL_F_Cu
                components_result.append(PlacedComponentInfo(
                    ref=comp_layout.ref, dest=comp_layout.position, angle_deg=comp_layout.angle_deg,
                    layer=comp_layer,
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

        return components_result, vias_result, tracks_result