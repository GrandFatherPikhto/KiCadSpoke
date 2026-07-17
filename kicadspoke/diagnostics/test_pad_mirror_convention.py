#!/usr/bin/env python3
"""
test_pad_mirror_convention.py — единственный эмпирический тест, способный
окончательно подтвердить или опровергнуть допущение в
pad_projection.predict_pad_position() про зеркалирование локального
смещения пада по оси X при флипе на другую сторону платы.

Использует адаптер KiCadSpoke и геометрию pad_projection.

Запуск:
    python -m kicadspoke.diagnostics.test_pad_mirror_convention C6 --pad 2
"""

import argparse
import sys
import time

from kipy.board_types import BoardLayer, Pad
from kipy.geometry import Vector2, Angle

from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.geometry.pad_projection import local_pad_offset, predict_pad_position
from kicadspoke.utils.units import MM

MM = 1_000_000


def step(label, func, *args, **kwargs):
    print(f"[...] {label}", flush=True)
    t0 = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        print(f"[OK]  {label} — {elapsed} мс", flush=True)
        return result
    except Exception as e:
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        print(f"[ERR] {label} — {elapsed} мс — {type(e).__name__}: {e}", flush=True)
        raise


def find_fp(adapter, ref):
    return adapter.get_footprint(ref)


def find_pad(fp, pad_number):
    return next((p for p in adapter.get_footprint_pads(fp) if p.number == pad_number), None)


def rotate_component(adapter, ref, delta_deg):
    """Поворачивает компонент на delta_deg относительно текущего угла (без флипа)."""
    fp = find_fp(adapter, ref)
    if fp is None:
        raise ValueError(f"Компонент {ref} не найден")
    commit = adapter.begin_commit()
    try:
        new_angle = Angle.from_degrees(fp.orientation.degrees + delta_deg)
        fp.orientation = new_angle
        adapter.update_items([fp])
        adapter.push_commit(commit, f"test_pad_mirror_convention: поворот {ref} на {delta_deg:+.1f}°")
    except Exception:
        adapter.drop_commit(commit)
        raise


def flip_component(adapter, ref):
    fp = find_fp(adapter, ref)
    if fp is None:
        raise ValueError(f"Компонент {ref} не найден")
    adapter.flip_selected([fp])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="refdes компонента для теста, НЕ боевой (например C6)")
    ap.add_argument("--pad", default="2", help="номер пада для отслеживания (по умолчанию GND, обычно '2')")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    args = ap.parse_args()

    adapter = step("KiCadBoardAdapter(...)", KiCadBoardAdapter, timeout_ms=args.timeout_ms)
    step("adapter.refresh_board()", adapter.refresh_board)

    fp0 = find_fp(adapter, args.ref)
    if fp0 is None:
        sys.exit(f"[ошибка] {args.ref} не найден на плате")
    pad0 = find_pad(fp0, args.pad)
    if pad0 is None:
        sys.exit(f"[ошибка] у {args.ref} нет пада {args.pad}")

    orig_pos = fp0.position
    orig_angle = fp0.orientation.degrees
    orig_layer = fp0.layer
    local_offset = local_pad_offset(fp0, pad0)

    print(f"\n=== Исходное состояние {args.ref} ===")
    print(f"позиция=({orig_pos.x/1e6:.3f},{orig_pos.y/1e6:.3f})мм угол={orig_angle:.1f}° "
          f"слой={'F.Cu' if orig_layer==BoardLayer.BL_F_Cu else 'B.Cu'}")
    print(f"локальный оффсет пада {args.pad}: ({local_offset.x/1e6:.3f}, {local_offset.y/1e6:.3f}) мм\n")

    # --- Шаг 1: поворот на 90°, БЕЗ флипа ---
    print("=== Шаг 1: поворот на +90°, БЕЗ флипа (проверка базовой формулы) ===")
    rotate_component(adapter, args.ref, 90.0)
    adapter.refresh_board()
    fp1 = find_fp(adapter, args.ref)
    pad1 = find_pad(fp1, args.pad)

    origin = Vector2.from_xy(0, 0)
    predicted_1 = fp1.position + local_offset.rotate(Angle.from_degrees(fp1.orientation.degrees), origin)
    real_1 = pad1.position
    dist_1_mm = ((predicted_1.x - real_1.x)**2 + (predicted_1.y - real_1.y)**2)**0.5 / 1e6
    print(f"Предсказано: ({predicted_1.x/1e6:.3f}, {predicted_1.y/1e6:.3f}) мм")
    print(f"Реально:     ({real_1.x/1e6:.3f}, {real_1.y/1e6:.3f}) мм")
    print(f"Расхождение: {dist_1_mm:.4f} мм {'-- OK, база верна' if dist_1_mm < 0.01 else '!! БАЗОВАЯ ФОРМУЛА НЕ СХОДИТСЯ, дальше проверять флип бессмысленно'}\n")

    # --- Шаг 2: флип на другую сторону ---
    print("=== Шаг 2: флип на другую сторону (проверка допущения о зеркалировании) ===")
    flip_component(adapter, args.ref)
    adapter.refresh_board()
    fp2 = find_fp(adapter, args.ref)
    pad2 = find_pad(fp2, args.pad)

    real_2 = pad2.position
    final_angle = fp2.orientation.degrees

    candidate_x_mirror = fp2.position + Vector2.from_xy(-local_offset.x, local_offset.y).rotate(
        Angle.from_degrees(final_angle), origin)
    candidate_y_mirror = fp2.position + Vector2.from_xy(local_offset.x, -local_offset.y).rotate(
        Angle.from_degrees(final_angle), origin)
    candidate_no_mirror = fp2.position + local_offset.rotate(Angle.from_degrees(final_angle), origin)

    dist_x = ((candidate_x_mirror.x - real_2.x)**2 + (candidate_x_mirror.y - real_2.y)**2)**0.5 / 1e6
    dist_y = ((candidate_y_mirror.x - real_2.x)**2 + (candidate_y_mirror.y - real_2.y)**2)**0.5 / 1e6
    dist_none = ((candidate_no_mirror.x - real_2.x)**2 + (candidate_no_mirror.y - real_2.y)**2)**0.5 / 1e6

    print(f"Реальное положение пада после флипа: ({real_2.x/1e6:.3f}, {real_2.y/1e6:.3f}) мм, угол={final_angle:.1f}°")
    print(f"Кандидат 'зеркало по X' (текущее допущение в коде): расхождение {dist_x:.4f} мм")
    print(f"Кандидат 'зеркало по Y':                            расхождение {dist_y:.4f} мм")
    print(f"Кандидат 'без зеркалирования':                       расхождение {dist_none:.4f} мм")

    results = [("зеркало по X (текущий код)", dist_x), ("зеркало по Y", dist_y), ("без зеркалирования", dist_none)]
    winner = min(results, key=lambda r: r[1])
    print(f"\n>>> ПОБЕДИТЕЛЬ: {winner[0]} (расхождение {winner[1]:.4f} мм)")
    if winner[0].startswith("зеркало по X"):
        print(">>> Текущий код в pad_projection.py УЖЕ ПРАВИЛЬНЫЙ, менять ничего не нужно.")
    else:
        print(f">>> Код в pad_projection.py нужно поправить: сейчас зеркалируется X, "
              f"а должно быть — {winner[0]}.")

    # --- Возврат в исходное состояние ---
    print("\n=== Возврат в исходное состояние ===")
    flip_component(adapter, args.ref)
    adapter.refresh_board()
    rotate_component(adapter, args.ref, -90.0)
    adapter.refresh_board()
    fp_final = find_fp(adapter, args.ref)
    print(f"Финальное состояние: угол={fp_final.orientation.degrees:.1f}° "
          f"(было {orig_angle:.1f}°), слой={'F.Cu' if fp_final.layer==BoardLayer.BL_F_Cu else 'B.Cu'} "
          f"(было {'F.Cu' if orig_layer==BoardLayer.BL_F_Cu else 'B.Cu'})")


if __name__ == "__main__":
    main()