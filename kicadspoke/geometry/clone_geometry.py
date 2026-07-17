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
from typing import List, Dict
from kipy.geometry import Vector2, Angle

from ..config import ClonePlacement, SpokeTemplate, TemplateVia
from ..exceptions import ValidationError, format_fatal_error
from ..net_resolution import resolve_net
from ..utils.units import MM
from .spoke_layout import local_to_absolute, ResolvedVia, ComponentLayout, SpokeLayout


def _resolve_clone_via(origin: Vector2, via: TemplateVia, rotation_deg: float,
                       clone: ClonePlacement) -> ResolvedVia:
    if via.net is None:
        raise ValidationError(format_fatal_error(
            f"via без цепи в шаблоне {clone.template!r} ({clone.name!r})",
            [f"via на (along={via.offset_along_mm}, across={via.offset_across_mm}) не имеет net — "
             f"для ClonePlacement нет цепи правила по умолчанию (в отличие от ManualSpoke), "
             f"net обязателен для каждой via в клонируемом шаблоне"]
        ))
    return ResolvedVia(
        position=local_to_absolute(origin, via.offset_along_mm, via.offset_across_mm, rotation_deg),
        net=resolve_net(via.net, clone.params, clone.net_overrides),
        drill_mm=via.drill_mm,
        diameter_mm=via.diameter_mm,
    )


def apply_clone_geometry(
    clone: ClonePlacement,
    template: SpokeTemplate,
    role_to_ref: Dict[str, str],
) -> SpokeLayout:
    """
    Считает абсолютные позиции всего, что есть в шаблоне, для конкретного
    ClonePlacement. role_to_ref — уже разрешённое СНАРУЖИ (см.
    clone_role_resolver.py) сопоставление роль->ref.
    """
    origin = Vector2.from_xy(int(clone.origin_x_mm * MM), int(clone.origin_y_mm * MM))
    rotation_deg = clone.rotation_deg

    layout = SpokeLayout(origin=origin)
    layout.vias = [_resolve_clone_via(origin, v, rotation_deg, clone) for v in template.vias]

    for slot in template.components:
        ref = role_to_ref.get(slot.role)
        if ref is None:
            continue
        layout.components.append(ComponentLayout(
            ref=ref,
            role=slot.role,
            position=local_to_absolute(origin, slot.offset_along_mm, slot.offset_across_mm, rotation_deg),
            angle_deg=slot.angle_deg + rotation_deg,
            vias=[_resolve_clone_via(origin, v, rotation_deg, clone) for v in slot.vias],
        ))

    return layout
