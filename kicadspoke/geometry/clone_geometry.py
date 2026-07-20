# kicadspoke/geometry/clone_geometry.py
"""
clone_geometry.py — разворачивает шаблон в абсолютные координаты платы
для ClonePlacement (TemplatePlacer), в отличие от spoke_layout.py:

  - origin = (origin_x_mm, origin_y_mm) НАПРЯМУЮ (нет пада, нет shift —
    это уже абсолютная точка, не смещение от чего-либо).
  - net каждой via резолвится через net_resolution.resolve_net()
    (params + net_overrides) — НЕТ понятия rule_net (у ClonePlacement,
    в отличие от Rule/ManualSpoke, нет единой "цепи правила" вообще).
    via.net=None здесь ФАТАЛЬНО — нет разумного дефолта, на который
    можно было бы упасть, в отличие от spoke_layout.py.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from kipy.geometry import Vector2, Angle

from ..config import ClonePlacement, SpokeTemplate, TemplateVia
from ..exceptions import ValidationError, format_fatal_error
from ..net_resolution import resolve_net
from ..utils.units import MM
from .spoke_layout import local_to_absolute, ResolvedVia, ComponentLayout, SpokeLayout


def _resolve_clone_via(origin: Vector2, via: TemplateVia, rotation_deg: float,
                       clone: ClonePlacement, mirror: bool = False) -> ResolvedVia:
    if via.net is None:
        raise ValidationError(format_fatal_error(
            f"via без цепи в шаблоне {clone.template!r} ({clone.name!r})",
            [f"via на (along={via.offset_along_mm}, across={via.offset_across_mm}) не имеет net — "
             f"для ClonePlacement нет цепи правила по умолчанию (в отличие от ManualSpoke), "
             f"net обязателен для каждой via в клонируемом шаблоне"]
        ))
    pos = local_to_absolute(origin, via.offset_along_mm, via.offset_across_mm, rotation_deg)
    if mirror:
        pos = _mirror_x(origin, pos)
    return ResolvedVia(
        position=pos,
        net=resolve_net(via.net, clone.params, clone.net_overrides),
        drill_mm=via.drill_mm,
        diameter_mm=via.diameter_mm,
    )


def _mirror_x(origin: Vector2, p: Vector2) -> Vector2:
    """X-зеркало точки относительно вертикальной оси через origin."""
    return Vector2.from_xy(2 * origin.x - p.x, p.y)


def apply_clone_geometry(
    clone: ClonePlacement,
    template: SpokeTemplate,
    role_to_ref: Dict[str, str],
    anchor_position: Optional[Vector2] = None,
    mirror: bool = False,
) -> SpokeLayout:
    """
    Считает абсолютные позиции всего, что есть в шаблоне, для конкретного
    ClonePlacement. role_to_ref — уже разрешённое СНАРУЖИ (см.
    clone_role_resolver.py) сопоставление роль->ref.

    anchor_position — абсолютная точка якоря (центр пада или футпринта из
    anchor_ref/anchor_pad), разрешённая СНАРУЖИ (calculator ходит в adapter,
    геометрия живую плату не трогает). Если задана — origin_x/y_mm работают
    как ПЛОСКИЙ сдвиг от неё (без поворота, ровно как shift у ManualSpoke);
    rotation_deg крутит только содержимое шаблона. Если None — origin_x/y_mm
    остаются абсолютной точкой платы, как раньше.

    mirror=True — размещение на ОБРАТНОЙ стороне: шаблон считается снятым
    с front, финальные позиции (после поворота) X-зеркалятся относительно
    вертикальной оси через origin, углы компонентов -> 180°−φ (конвенция
    B.Cu из декап-плейсера). Якорный сдвиг origin_x/y_mm НЕ зеркалится —
    он в координатах платы, как shift у ManualSpoke. Сами футпринты на
    B.Cu переворачивает FlipManager (executor ставит абсолютный угол
    ПОСЛЕ флипа, так что +180° от флипа здесь учитывать не надо).
    """
    shift = Vector2.from_xy(int(clone.origin_x_mm * MM), int(clone.origin_y_mm * MM))
    if anchor_position is not None:
        origin = Vector2.from_xy(anchor_position.x + shift.x, anchor_position.y + shift.y)
    else:
        origin = shift
    rotation_deg = clone.rotation_deg

    def place(along_mm: float, across_mm: float) -> Vector2:
        p = local_to_absolute(origin, along_mm, across_mm, rotation_deg)
        return _mirror_x(origin, p) if mirror else p

    def comp_angle(angle_deg: float) -> float:
        phi = angle_deg + rotation_deg
        return (180.0 - phi) % 360.0 if mirror else phi

    layout = SpokeLayout(origin=origin)
    layout.vias = [_resolve_clone_via(origin, v, rotation_deg, clone, mirror) for v in template.vias]

    for slot in template.components:
        ref = role_to_ref.get(slot.role)
        if ref is None:
            continue
        layout.components.append(ComponentLayout(
            ref=ref,
            role=slot.role,
            position=place(slot.offset_along_mm, slot.offset_across_mm),
            angle_deg=comp_angle(slot.angle_deg),
            vias=[_resolve_clone_via(origin, v, rotation_deg, clone, mirror) for v in slot.vias],
            slot_layer=slot.layer,
        ))

    return layout
