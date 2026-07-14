# decap_placer/optimization/nlp_optimizer.py
"""
Упрощённый NLP-оптимизатор для полировки раскладки конденсаторов.
Использует scipy.optimize.minimize (SLSQP) для минимизации:
- отклонения от идеальных радиальных позиций (с весами по цепям)
- штрафов за пересечения (квадратичный пенальти)
"""

import math
import logging
from typing import List, Tuple, Optional
import numpy as np
from scipy.optimize import minimize, Bounds

from kipy.geometry import Vector2
from kipy.board_types import BoardLayer, FootprintInstance

from .interfaces import IOptimizer, RawPlacement, FinalPlacement
from ..config import Config, SpokeComponent
from ..kicad.adapter import KiCadBoardAdapter
from ..utils.units import MM
from ..exceptions import GeometryError

logger = logging.getLogger(__name__)

class NLPOptimizer(IOptimizer):
    """
    Упрощённый NLP-оптимизатор.
    Оптимизирует позиции центров конденсаторов (x, y) и их углы поворота.
    Целевая функция: сумма взвешенных квадратов отклонений от идеальных позиций
    + квадратичный штраф за пересечения.
    """

    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config
        self.max_iter = getattr(config, 'nlp_max_iterations', 100)
        self.tol = getattr(config, 'nlp_tolerance', 1e-6)
        # Радиусы для проверки пересечений (в мм) - можно брать из конфига или вычислять
        self.cap_radius_mm = 0.5   # типовой радиус для 0402
        self.clearance_mm = 0.15   # зазор

    def optimize(self, initial_placements: List[RawPlacement],
                 target_fp: FootprintInstance,
                 boundary_polygon: List[Vector2],
                 rules: List,
                 side: str,
                 target_layer: BoardLayer) -> List[FinalPlacement]:
        """
        Запускает оптимизацию.
        Если initial_placements пуст – генерирует начальное приближение через эвристику.
        Возвращает оптимизированные позиции.
        """
        if not initial_placements:
            from .heuristic_optimizer import HeuristicOptimizer
            heuristic = HeuristicOptimizer(self.adapter, self.cfg)
            final_placements = heuristic.optimize([], target_fp, boundary_polygon, rules, side, target_layer)
            # Преобразуем FinalPlacement в RawPlacement
            initial_placements = [
                RawPlacement(fp.component, fp.position, fp.direction, fp.angle)
                for fp in final_placements
            ]

        logger.info(f"Запуск NLP-оптимизации для {len(initial_placements)} конденсаторов")

        # 1. Подготовка данных
        # Собираем информацию о каждом конденсаторе: компонент, идеальная позиция,
        # начальная позиция, угол, направление, вес цепи.
        n = len(initial_placements)
        comps = []
        ideal_positions = []   # список Vector2 (идеальные позиции из эвристики)
        initial_positions = [] # список Vector2 (начальные позиции, могут совпадать с идеальными)
        initial_angles = []    # список float (углы в градусах)
        directions = []        # список (dx, dy)
        weights = []           # вес для целевой функции (по цепи)

        for p in initial_placements:
            comps.append(p.component)
            ideal_positions.append(p.position)
            initial_positions.append(p.position)   # пока считаем, что начальные = идеальные
            initial_angles.append(p.angle)
            directions.append(p.direction)
            # Вес: для VCC_INT/VCCD_PLL больше, для остальных меньше (можно вынести в конфиг)
            net_name = self._get_net_for_component(p.component, rules)
            if net_name in ("+1V2_VCCINT", "+1V2_VCCD_PLL"):
                weights.append(10.0)
            elif net_name in ("+2V5_VCCA", "+3V3_VCCIO"):
                weights.append(1.0)
            else:
                weights.append(1.0)

        # 2. Собираем вектор переменных x = [x1, y1, angle1, x2, y2, angle2, ...]
        # (все в мм для удобства, но можно и в нанометрах)
        x0 = []
        for i in range(n):
            x0.append(initial_positions[i].x / MM)   # переводим в мм
            x0.append(initial_positions[i].y / MM)
            x0.append(initial_angles[i])            # угол в градусах
        x0 = np.array(x0)

        # 3. Определяем целевую функцию
        def objective(x):
            total = 0.0
            # Штраф за отклонение от идеальной позиции
            for i in range(n):
                idx = 3*i
                x_mm = x[idx]
                y_mm = x[idx+1]
                angle = x[idx+2]
                # Идеальная позиция
                ix = ideal_positions[i].x / MM
                iy = ideal_positions[i].y / MM
                dx = x_mm - ix
                dy = y_mm - iy
                total += weights[i] * (dx*dx + dy*dy)
                # Штраф за изменение угла (опционально)
                # total += 0.01 * (angle - initial_angles[i])**2

            # Штраф за пересечения (квадратичный пенальти)
            penalty_scale = 100.0  # коэффициент штрафа (можно подбирать)
            for i in range(n):
                idx_i = 3*i
                xi = x[idx_i]
                yi = x[idx_i+1]
                for j in range(i+1, n):
                    idx_j = 3*j
                    xj = x[idx_j]
                    yj = x[idx_j+1]
                    dx = xi - xj
                    dy = yi - yj
                    dist = math.sqrt(dx*dx + dy*dy)
                    min_dist = 2 * self.cap_radius_mm + self.clearance_mm
                    if dist < min_dist:
                        # Квадратичный штраф за нарушение
                        violation = (min_dist - dist) / min_dist  # нормализованное нарушение
                        total += penalty_scale * violation * violation
            return total

        # 4. Границы (bounds) – координаты не должны уходить слишком далеко от зоны
        # Пока зададим широкие границы: +/- 10 мм от идеальной позиции
        bounds = []
        for i in range(n):
            ix = ideal_positions[i].x / MM
            iy = ideal_positions[i].y / MM
            bounds.append((ix - 10.0, ix + 10.0))  # x
            bounds.append((iy - 10.0, iy + 10.0))  # y
            bounds.append((initial_angles[i] - 90.0, initial_angles[i] + 90.0))  # угол

        # 5. Запускаем оптимизацию
        logger.info("Запуск scipy.optimize.minimize (SLSQP)")
        result = minimize(
            objective,
            x0,
            method='SLSQP',
            bounds=bounds,
            options={'maxiter': self.max_iter, 'ftol': self.tol, 'disp': False}
        )

        if not result.success:
            logger.warning(f"NLP оптимизация не сошлась: {result.message}")
        else:
            logger.info(f"NLP оптимизация завершена успешно, функция={result.fun:.3f}")

        # 6. Извлекаем оптимизированные позиции
        x_opt = result.x
        optimized = []
        for i in range(n):
            x_mm = x_opt[3*i]
            y_mm = x_opt[3*i+1]
            angle = x_opt[3*i+2]
            new_pos = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
            # Направление оставляем исходным (или можно пересчитать из угла)
            direction = directions[i]
            optimized.append(FinalPlacement(comps[i], new_pos, direction, angle))

        logger.info(f"NLP оптимизация завершена, обработано {len(optimized)} конденсаторов")
        return optimized

    def _get_net_for_component(self, component: SpokeComponent, rules: List) -> str:
        """Находит цепь для компонента по правилам."""
        for rule in rules:
            for spoke in rule.spokes:
                if any(c.ref == component.ref for c in spoke.components):
                    return rule.net
        return "UNKNOWN"