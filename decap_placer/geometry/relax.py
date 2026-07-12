# decap_placer/geometry/relax.py

from typing import List, Tuple, TypeVar, Callable
from kipy.geometry import Vector2

T = TypeVar("T")


def get_tangential_axis(normal: Tuple[float, float]) -> str:
    """
    Определяет, какая координата — "вдоль ряда" (тангенциальная) для
    данной нормали стороны зоны. Нормаль после BoundaryStrategy всегда
    осевая: (±1, 0) — левая/правая сторона (ряд идёт по Y), (0, ±1) —
    верхняя/нижняя (ряд идёт по X).
    """
    nx, ny = normal
    return "y" if abs(nx) > abs(ny) else "x"


def relax_1d(items: List[Tuple[float, T]], min_gap: float, max_iterations: int = 10) -> List[Tuple[float, T]]:
    """
    Раздвигает точки вдоль одной оси так, чтобы расстояние между
    соседними (после сортировки) было не меньше min_gap.

    Алгоритм: точки группируются в кластеры — последовательности, где
    соседи ближе min_gap друг к другу. Внутри кластера точки
    переставляются РАВНОМЕРНО с шагом min_gap, ЦЕНТРИРОВАННО на исходном
    центре тяжести кластера (чтобы не было систематического сноса всей
    группы в одну сторону). Точки без конфликтов (кластер из одной точки)
    не трогаются вообще.

    После одного прохода кластеризации-и-раздвижки могут появиться новые
    нарушения НА ГРАНИЦАХ кластеров (раздвинутый кластер мог придвинуться
    к соседнему) — поэтому процесс повторяется до max_iterations раз или
    пока не останется нарушений.

    items: список (координата_вдоль_ряда_в_нм, произвольная_нагрузка)
    Возвращает новый список той же длины и того же порядка нагрузок
    (порядок в списке НЕ гарантированно совпадает с исходным — сортировка
    по координате внутренняя), но с той же нагрузкой на новую координату.
    """
    current = list(items)
    for _ in range(max_iterations):
        current = sorted(current, key=lambda p: p[0])
        n = len(current)
        if n <= 1:
            return current

        # Есть ли хоть одно нарушение?
        violated = any(current[i + 1][0] - current[i][0] < min_gap for i in range(n - 1))
        if not violated:
            return current

        # Кластеризация: последовательные точки с зазором < min_gap — один кластер
        clusters: List[List[Tuple[float, T]]] = []
        bucket = [current[0]]
        for i in range(1, n):
            if current[i][0] - bucket[-1][0] < min_gap:
                bucket.append(current[i])
            else:
                clusters.append(bucket)
                bucket = [current[i]]
        clusters.append(bucket)

        new_current = []
        for cluster in clusters:
            if len(cluster) == 1:
                new_current.append(cluster[0])
                continue
            centroid = sum(t for t, _ in cluster) / len(cluster)
            span = (len(cluster) - 1) * min_gap
            start = centroid - span / 2.0
            for i, (_, payload) in enumerate(cluster):
                new_current.append((start + i * min_gap, payload))
        current = new_current

    return sorted(current, key=lambda p: p[0])


def relax_positions(entries: List[Tuple[Vector2, Tuple[float, float], T]],
                     min_gap_mm: float, mm_per_unit: int) -> List[Tuple[Vector2, T]]:
    """
    Высокоуровневая обёртка над relax_1d для точек в плоскости платы.

    entries: список (позиция Vector2, нормаль (nx,ny), нагрузка).
    Группирует записи по (нормаль, перпендикулярная координата, окр. до
    0.001мм) — т.е. по одному "ряду" (одна сторона зоны, одно значение
    offset_mm/placement дают одну и ту же линию), затем раздвигает каждую
    группу вдоль тангенциальной оси.

    Возвращает список (новая_позиция, нагрузка) в том же порядке, что и
    map по нагрузке (порядок между группами не гарантирован, но нагрузка
    сохраняется 1:1 — вызывающий код сопоставляет обратно по identity/ref
    нагрузки, а не по индексу).
    """
    min_gap = min_gap_mm * mm_per_unit

    groups: dict = {}
    for pos, normal, payload in entries:
        axis = get_tangential_axis(normal)
        perp_coord = pos.x if axis == "y" else pos.y
        # округляем перпендикулярную координату, чтобы объекты на "одной
        # линии" (тот же offset_mm) гарантированно попали в одну группу
        # несмотря на возможные ошибки округления до нм
        key = (normal, round(perp_coord / 1000) * 1000)
        groups.setdefault(key, []).append((pos, payload))

    result: List[Tuple[Vector2, T]] = []
    for (normal, _perp), points in groups.items():
        axis = get_tangential_axis(normal)
        items = []
        for pos, payload in points:
            t = pos.y if axis == "y" else pos.x
            items.append((t, (pos, payload)))

        relaxed = relax_1d(items, min_gap)

        for new_t, (orig_pos, payload) in relaxed:
            if axis == "y":
                new_pos = Vector2.from_xy(int(orig_pos.x), int(new_t))
            else:
                new_pos = Vector2.from_xy(int(new_t), int(orig_pos.y))
            result.append((new_pos, payload))

    return result
