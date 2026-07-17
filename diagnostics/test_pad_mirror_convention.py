#!/usr/bin/env python3
"""
test_pad_mirror_convention.py — единственный эмпирический тест, способный
окончательно подтвердить или опровергнуть допущение в
pad_projection.predict_pad_position() про зеркалирование локального
смещения пада по оси X при флипе на другую сторону платы.

Сам код (power_pin_orienter.py, via_planner.py) честно помечает это
допущение как непроверенное — этот скрипт проверяет.

Схема (два изолированных шага, чтобы не путать эффект поворота с
эффектом флипа):

  Шаг 1 — ТОЛЬКО поворот на 90°, БЕЗ флипа (тот же слой). Предсказание
  здесь тривиально должно совпасть с реальностью — это просто проверка,
  что сама база (local_pad_offset + rotate) верна, до того как в дело
  вступает вопрос про флип.

  Шаг 2 — ЗАТЕМ флип на другую сторону (угол при этом меняется по
  правилу 180-φ — это отдельно уже подтверждено ранее в проекте).
  Сравниваем РЕАЛЬНОЕ положение пада после флипа с ДВУМЯ кандидатами:
  зеркалирование по X (текущее допущение в коде) и зеркалирование по Y
  (альтернатива) — какой из них совпадёт с реальностью, тот и верен.

В конце компонент возвращается в ИСХОДНОЕ состояние (обратный флип +
обратный поворот) — плата не остаётся мутированной.

Запуск:
    python test_pad_mirror_convention.py C6 --pad 2
"""
import argparse
import sys
import time

import kipy
from kipy.board_types import BoardLayer
from kipy.geometry import Vector2, Angle

sys.path.insert(0, "..")
from decap_placer.geometry.pad_projection import local_pad_offset


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


def find_fp(board, ref):
    return next((fp for fp in board.get_footprints() if fp.reference_field.text.value == ref), None)


def find_pad(fp, pad_number):
    from kipy.board_types import Pad
    return next((p for p in fp.definition.items if isinstance(p, Pad) and p.number == pad_number), None)


def rotate_by(board, kicad, ref, delta_deg):
    """Поворачивает компонент на delta_deg относительно текущего угла (без флипа)."""
    fp = find_fp(board, ref)
    commit = board.begin_commit()
    try:
        new_angle = Angle.from_degrees(fp.orientation.degrees + delta_deg)
        fp.orientation = new_angle
        board.update_items([fp])
        board.push_commit(commit, f"test_pad_mirror_convention: поворот {ref} на {delta_deg:+.1f}°")
    except Exception:
        board.drop_commit(commit)
        raise


def flip(board, kicad, ref):
    fp = find_fp(board, ref)
    board.clear_selection()
    board.add_to_selection([fp])
    kicad.run_action("pcbnew.InteractiveEdit.flip")
    board.clear_selection()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="refdes компонента для теста, НЕ боевой (например C6)")
    ap.add_argument("--pad", default="2", help="номер пада для отслеживания (по умолчанию GND, обычно '2')")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    args = ap.parse_args()

    kicad = step("kipy.KiCad(...)", kipy.KiCad, timeout_ms=args.timeout_ms)
    board = step("kicad.get_board()", kicad.get_board)

    fp0 = find_fp(board, args.ref)
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
    rotate_by(board, kicad, args.ref, 90.0)
    board = step("kicad.get_board() (обновление)", kicad.get_board)
    fp1 = find_fp(board, args.ref)
    pad1 = find_pad(fp1, args.pad)

    origin = Vector2.from_xy(0, 0)
    predicted_1 = fp1.position + local_offset.rotate(Angle.from_degrees(fp1.orientation.degrees), origin)
    real_1 = pad1.position
    dist_1_mm = ((predicted_1.x-real_1.x)**2 + (predicted_1.y-real_1.y)**2)**0.5 / 1e6
    print(f"Предсказано: ({predicted_1.x/1e6:.3f}, {predicted_1.y/1e6:.3f}) мм")
    print(f"Реально:     ({real_1.x/1e6:.3f}, {real_1.y/1e6:.3f}) мм")
    print(f"Расхождение: {dist_1_mm:.4f} мм {'-- OK, база верна' if dist_1_mm < 0.01 else '!! БАЗОВАЯ ФОРМУЛА НЕ СХОДИТСЯ, дальше проверять флип бессмысленно'}\n")

    # --- Шаг 2: флип на другую сторону ---
    print("=== Шаг 2: флип на другую сторону (проверка допущения о зеркалировании) ===")
    flip(board, kicad, args.ref)
    board = step("kicad.get_board() (обновление)", kicad.get_board)
    fp2 = find_fp(board, args.ref)
    pad2 = find_pad(fp2, args.pad)

    real_2 = pad2.position
    final_angle = fp2.orientation.degrees

    candidate_x_mirror = fp2.position + Vector2.from_xy(-local_offset.x, local_offset.y).rotate(
        Angle.from_degrees(final_angle), origin)
    candidate_y_mirror = fp2.position + Vector2.from_xy(local_offset.x, -local_offset.y).rotate(
        Angle.from_degrees(final_angle), origin)
    candidate_no_mirror = fp2.position + local_offset.rotate(Angle.from_degrees(final_angle), origin)

    dist_x = ((candidate_x_mirror.x-real_2.x)**2 + (candidate_x_mirror.y-real_2.y)**2)**0.5 / 1e6
    dist_y = ((candidate_y_mirror.x-real_2.x)**2 + (candidate_y_mirror.y-real_2.y)**2)**0.5 / 1e6
    dist_none = ((candidate_no_mirror.x-real_2.x)**2 + (candidate_no_mirror.y-real_2.y)**2)**0.5 / 1e6

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
    flip(board, kicad, args.ref)
    board = step("kicad.get_board() (обновление)", kicad.get_board)
    rotate_by(board, kicad, args.ref, -90.0)
    board = step("kicad.get_board() (обновление)", kicad.get_board)
    fp_final = find_fp(board, args.ref)
    print(f"Финальное состояние: угол={fp_final.orientation.degrees:.1f}° "
          f"(было {orig_angle:.1f}°), слой={'F.Cu' if fp_final.layer==BoardLayer.BL_F_Cu else 'B.Cu'} "
          f"(было {'F.Cu' if orig_layer==BoardLayer.BL_F_Cu else 'B.Cu'})")


if __name__ == "__main__":
    main()
