# kicadspoke/geometry/spoke_layout.py
"""
spoke_layout.py — разворачивает шаблон спицы в абсолютные координаты платы.

Порядок применения (зафиксирован в разговоре с пользователем):
  1. Сдвиг (shift_x_mm, shift_y_mm) от центра пада FPGA к нулю спицы —
     обычный плоский перенос, БЕЗ поворота.
  2. Поворот получившегося нуля (и всего содержимого шаблона) на
     rotation_deg — как единое жёсткое тело.

Оба шага — в обычных координатах KiCad. Внутреннее содержимое шаблона
(along/across) описано один раз при rotation_deg=0 (условный эталонный
борт) и одинаково для любой спицы, использующей этот шаблон — поворот
на месте конкретной спицы полностью снимает необходимость менять знаки
смещений вручную под конкретный борт корпуса.

Использует ТУ ЖЕ формулу поворота, что и весь остальной проект
(kipy.geometry.Vector2.rotate(), эмпирически подтверждённую ранее для
конвенции флипа) — не переизобретает вращение самостоятельно.

ИЗМЕНЕНО (KiCadSpoke, обобщённые via): раньше via уровня компонента
("GND via") считалась от РЕАЛЬНОГО земляного пада уже размещённого
компонента — требовало чтения живой платы после коммита перемещений.
Теперь via (и уровня спицы, и уровня компонента) — ВСЕГДА чистая
геометрия от нуля спицы, той же формулой, что и позиция самого
компонента. Никакой зависимости от живой платы для via больше нет.
"""
from dataclasses import dataclass, field
from typing import List, Dict
from kipy.geometry import Vector2, Angle

from ..config import ManualSpoke, SpokeTemplate, TemplateVia
from ..utils.units import MM

_ORIGIN = Vector2.from_xy(0, 0)


def rotate_local_offset(along_mm: float, across_mm: float, rotation_deg: float) -> Vector2:
    """
    Поворачивает локальный вектор (along, across) на rotation_deg вокруг
    (0,0) — без переноса, просто повёрнутый вектор смещения в нанометрах.
    """
    local_vec = Vector2.from_xy(int(along_mm * MM), int(across_mm * MM))
    return local_vec.rotate(Angle.from_degrees(rotation_deg), _ORIGIN)


def local_to_absolute(origin: Vector2, along_mm: float, across_mm: float, rotation_deg: float) -> Vector2:
    """origin (уже после shift) + повёрнутый локальный оффсет (along, across)."""
    rotated = rotate_local_offset(along_mm, across_mm, rotation_deg)
    return Vector2.from_xy(origin.x + rotated.x, origin.y + rotated.y)


@dataclass
class ResolvedVia:
    """Полностью разрешённая via — абсолютная позиция, net уже не None."""
    position: Vector2
    net: str
    drill_mm: float
    diameter_mm: float


def _resolve_via(origin: Vector2, via: TemplateVia, rotation_deg: float, rule_net: str) -> ResolvedVia:
    return ResolvedVia(
        position=local_to_absolute(origin, via.offset_along_mm, via.offset_across_mm, rotation_deg),
        net=via.net or rule_net,
        drill_mm=via.drill_mm,
        diameter_mm=via.diameter_mm,
    )


@dataclass
class ResolvedTrack:
    """Полностью разрешённый прямой отрезок дорожки — обе точки уже абсолютные, net уже не None."""
    start: Vector2
    end: Vector2
    width_mm: float
    net: str
    layer: str  # 'F.Cu' | 'B.Cu', абсолютный — уже разрешён (свой или слоя шаблона, с учётом mirror)


@dataclass
class ComponentLayout:
    ref: str
    role: str
    position: Vector2
    angle_deg: float
    vias: List[ResolvedVia] = field(default_factory=list)
    slot_layer: str = None     # абсолютный слой слота ('F.Cu'/'B.Cu'), None = слой шаблона


@dataclass
class SpokeLayout:
    origin: Vector2                                  # ноль спицы (после shift, до поворота)
    vias: List[ResolvedVia] = field(default_factory=list)     # via уровня спицы (была power_via)
    components: List[ComponentLayout] = field(default_factory=list)
    tracks: List[ResolvedTrack] = field(default_factory=list)  # только у ClonePlacement (clone_geometry.py); ManualSpoke не заполняет


def apply_spoke_geometry(
    pad_position: Vector2,
    spoke: ManualSpoke,
    template: SpokeTemplate,
    rule_net: str,
    role_to_ref: Dict[str, str],
) -> SpokeLayout:
    """
    Считает абсолютные позиции ВСЕГО, что есть в шаблоне для данной
    спицы, включая via обоих уровней — чистая геометрия, никакого
    обращения к живой плате. role_to_ref — уже разрешённое СНАРУЖИ (см.
    component_pool.py) сопоставление роль->ref; эта функция сама не
    решает, какой ref взять на какую роль — только геометрия.
    """
    origin = Vector2.from_xy(
        pad_position.x + int(spoke.shift_x_mm * MM),
        pad_position.y + int(spoke.shift_y_mm * MM),
    )

    layout = SpokeLayout(origin=origin)

    layout.vias = [_resolve_via(origin, v, spoke.rotation_deg, rule_net) for v in template.vias]

    for slot in template.components:
        ref = role_to_ref.get(slot.role)
        if ref is None:
            continue
        layout.components.append(ComponentLayout(
            ref=ref,
            role=slot.role,
            position=local_to_absolute(origin, slot.offset_along_mm, slot.offset_across_mm, spoke.rotation_deg),
            angle_deg=slot.angle_deg + spoke.rotation_deg,
            vias=[_resolve_via(origin, v, spoke.rotation_deg, rule_net) for v in slot.vias],
            slot_layer=slot.layer,
        ))

    return layout