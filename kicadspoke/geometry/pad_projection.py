# kicadspoke/geometry/pad_projection.py
"""
pad_projection.py — предсказание, где окажется конкретный пад компонента,
если сам компонент перенести в новую позицию dest и повернуть на новый
угол angle_deg (уже с учётом зеркалирования на back-слой, если оно
применяется к angle_deg отдельно, выше по стеку).

Раньше эта логика (и её баг) существовала в ДВУХ местах одновременно:
power_pin_orienter.py (для выбора facing) и негласно предполагалась (но
НЕ применялась) в via_planner.py (для позиции stitching-виа — там вместо
неё использовался просто "старый абсолютный оффсет пада, перенесённый как
есть", что и приводило к виа на площадках конденсаторов при любом
изменении угла). Теперь один источник для обоих потребителей — если
конвенция флипа окажется неверной, чинить в одном месте, а не в двух.

ВАЖНО: needs_flip=True (зеркалирование локального смещения по оси X)
— это ПОКА НЕПОДТВЕРЖДЁННОЕ эмпирически допущение. См.
diagnose/test_pad_mirror_convention.py — однократный, но окончательный
тест на реальной плате, сравнивающий это предсказание с тем, что
реально показывает KiCad после настоящего флипа+поворота.
"""
from kipy.board_types import FootprintInstance, Pad
from kipy.geometry import Vector2, Angle


def local_pad_offset(fp: FootprintInstance, pad: Pad) -> Vector2:
    """
    Смещение пада относительно центра футпринта в ЕГО СОБСТВЕННОЙ,
    неповёрнутой системе координат — то есть постоянный, не зависящий от
    текущего угла факт о геометрии футпринта. Получается «отменой»
    текущего угла поворота у уже известного абсолютного смещения.
    """
    origin = Vector2.from_xy(0, 0)
    diff = pad.position - fp.position
    return diff.rotate(Angle.from_degrees(-fp.orientation.degrees), origin)


def predict_pad_position(
    fp: FootprintInstance,
    pad: Pad,
    dest: Vector2,
    angle_deg: float,
    needs_flip: bool,
) -> Vector2:
    """
    Предсказывает АБСОЛЮТНУЮ позицию pad, если fp переместить в dest и
    повернуть на angle_deg (само по себе уже итоговое, включая
    зеркалирование угла для back-слоя, если оно требовалось — здесь
    ничего дополнительно с самим angle_deg не делается).

    needs_flip: True, если fp физически переезжает на другую сторону
    платы в этом прогоне (текущий fp.layer отличается от целевого слоя).
    В этом случае локальное смещение пада зеркалируется по оси X ДО
    поворота на новый угол — так плата "видна с обратной стороны".
    """
    origin = Vector2.from_xy(0, 0)
    offset = local_pad_offset(fp, pad)
    if needs_flip:
        offset = Vector2.from_xy(-offset.x, offset.y)
    rotated = offset.rotate(Angle.from_degrees(angle_deg), origin)
    return dest + rotated
