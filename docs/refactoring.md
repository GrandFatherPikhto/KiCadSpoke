Понял. Нам нужен **рефакторинг**, который сделает проект гибким для дальнейших изменений (keepout, power_pin_facing, NLP, новые стратегии), а не «перепишем всё заново». Ниже — **план поэтапного рефакторинга** с приоритетами, конкретными действиями и примерами кода.

---

## Стратегия рефакторинга

1. **Не ломать работающую функциональность** – рефакторинг делаем итеративно, с сохранением обратной совместимости на каждом шаге.
2. **Выделять чёткие интерфейсы** – чтобы можно было подменять реализации (тесты, новые алгоритмы).
3. **Разделять ответственность** – каждый класс/модуль делает только одно.
4. **Убирать дублирование и мёртвый код**.
5. **Подготовить почву для NLP-модели** – сделать так, чтобы её можно было вставить как альтернативный «оптимизатор» без изменения всего остального.

---

## Этап 0. Подготовительный (быстро, без изменения логики)

- **Перенести `resolve_power_pin_facing()` из `config.py` в `placement/power_pin.py`** – чтобы `config.py` оставался только моделью данных.
- **Удалить неиспользуемый параметр `repeat_fan_step_mm` из `RulesGenerator` и CLI** (или пометить `@deprecated`, но лучше убрать).
- **Вынести константы `step_mm`, `max_radius_mm`, `n_directions` из `find_free_point` в `Config`** – чтобы они стали настраиваемыми.
- **Создать абстрактный класс `IBoardAdapter`** в `kicad/interfaces.py` и заставить `KiCadBoardAdapter` его реализовать. Это позволит в будущем подменять адаптер для тестов.

Пример интерфейса:

```python
# kicad/interfaces.py
from abc import ABC, abstractmethod
from typing import List, Optional, Any
from kipy.board_types import FootprintInstance, Zone, Net, Pad, Via
from kipy.geometry import Vector2

class IBoardAdapter(ABC):
    @abstractmethod
    def refresh_board(self): ...
    @abstractmethod
    def get_footprint(self, ref: str) -> Optional[FootprintInstance]: ...
    @abstractmethod
    def get_footprints(self) -> List[FootprintInstance]: ...
    @abstractmethod
    def get_footprint_pads(self, fp: FootprintInstance) -> List[Pad]: ...
    @abstractmethod
    def get_pad_by_number(self, fp: FootprintInstance, number: str) -> Optional[Pad]: ...
    @abstractmethod
    def get_zone_by_name(self, name: str) -> Optional[Zone]: ...
    @abstractmethod
    def get_net_by_name(self, name: str) -> Optional[Net]: ...
    @abstractmethod
    def get_bounding_boxes(self, items) -> List[Optional[Any]]: ...
    @abstractmethod
    def begin_commit(self): ...
    @abstractmethod
    def push_commit(self, commit, description: str): ...
    @abstractmethod
    def drop_commit(self, commit): ...
    @abstractmethod
    def update_items(self, items): ...
    @abstractmethod
    def create_items(self, items): ...
    @abstractmethod
    def flip_selected(self, footprints: List[FootprintInstance]): ...
    @abstractmethod
    def commit_with_retry(self, description: str, work_fn, retries: int = 1) -> bool: ...
    @abstractmethod
    def create_via(self, position: Vector2, net: Net, drill_mm: float, diameter_mm: float) -> Via: ...
```

---

## Этап 1. Разделение `PlacementPlanner` на сервисы (ключевой)

Сейчас `PlacementPlanner` делает всё. Выделим несколько независимых классов:

### 1.1. `PositionCalculator` – вычисляет сырые позиции и углы
- Ответственность: для каждого компонента по стратегии и правилам выдать `(position, direction, angle)`.
- Метод: `compute_raw_positions(rules: List[Rule], target_fp, boundary_polygon, side, rotation_mode, fixed_angle) -> List[RawPlacement]`, где `RawPlacement` – именованный кортеж.
- Использует стратегии (можно передавать фабрику стратегий).

### 1.2. `SpacingRelaxer` – раздвижка вдоль ряда
- Ответственность: принять список `(position, direction, payload)` и вернуть скорректированные позиции с тем же payload.
- Метод: `relax(entries, min_gap_mm) -> List[RelaxedPosition]`.
- Использует `relax_positions` из `geometry.relax`.

### 1.3. `PowerPinOrienter` – уточнение угла для power_pin_facing
- Ответственность: для каждого компонента скорректировать угол, чтобы силовой вывод смотрел в нужную сторону.
- Метод: `adjust_angles(raw_placements, target_fp, adapter) -> List[AdjustedPlacement]`.
- Внутри использует `resolve_power_pin_facing` и эмпирический подбор угла (можно вынести логику `_resolve_facing_angle` сюда).

### 1.4. `KeepoutBuilder` – построение keepout-областей
- Ответственность: по списку футпринтов и падов (или refdes) построить список Rect.
- Метод: `build_keepout(target_ref, cap_refs, adapter, clearance_mm) -> List[Rect]`.
- Уже есть `_build_via_keepout`, просто вынести.

### 1.5. `ViaPlanner` – планирование stitching via и термовиа
- Ответственность: на основе финальных позиций компонентов и конфига via/thermal_via_array сгенерировать `ViaCommand` с учётом keepout.
- Метод: `plan_vias(placements, keepout, via_config, thermal_config, adapter, zone_center) -> List[ViaCommand]`.
- Использует `_plan_stitching_vias`, `_zone_preferred_direction`, `_plan_thermal_vias` (перенести сюда).

### 1.6. `PlacementPlanner` (новый) – координатор
- В конструктор принимает все вышеперечисленные сервисы (или фабрику для них).
- Метод `plan_moves()` вызывает цепочку: PositionCalculator → PowerPinOrienter → SpacingRelaxer → возвращает `MoveCommand`.
- Метод `plan_vias()` вызывает ViaPlanner (после того, как перемещения закоммичены и плата обновлена).
- Таким образом, `PlacementPlanner` становится тонким оркестратором, а не монолитом.

**Пример интерфейса нового `PlacementPlanner`**:

```python
class PlacementPlanner:
    def __init__(self, adapter: IBoardAdapter, config: Config,
                 position_calc: PositionCalculator,
                 orienter: PowerPinOrienter,
                 relaxer: SpacingRelaxer,
                 via_planner: ViaPlanner):
        ...
    def plan_moves(self) -> List[MoveCommand]: ...
    def plan_vias(self) -> List[ViaCommand]: ...
```

---

## Этап 2. Выделение фабрик и конфигурации

- **Фабрика стратегий** – чтобы можно было добавлять новые стратегии без изменения `PositionCalculator`.
  ```python
  class StrategyFactory:
      @staticmethod
      def create(mode: str, fixed_angle: float = 0.0) -> PlacementStrategy: ...
  ```
- **Конфигурация** – все параметры, которые сейчас жёстко зашиты (шаг поиска, радиус поиска, количество направлений), вынести в `Config` и читать из YAML.

---

## Этап 3. Улучшение тестируемости

- Заменить прямые вызовы `adapter.get_footprint()` на интерфейс `IBoardAdapter` – теперь можно подставить mock.
- Сделать все сервисы независимыми от адаптера там, где это возможно (например, `PositionCalculator` не требует адаптера, если ему передать все данные).
- Для `KeepoutBuilder` и `ViaPlanner` адаптер нужен для bbox и создания via – но через интерфейс это легко мокается.

---

## Этап 4. Подготовка к NLP-оптимизации

- Ввести абстракцию **`Optimizer`** – интерфейс, который принимает начальное приближение (список позиций) и возвращает оптимизированные позиции.
- Эвристический алгоритм (текущий `relax` + keepout) будет одной реализацией.
- NLP-решатель (`scipy.optimize`) – второй реализацией.
- В `PlacementPlanner` можно будет выбирать оптимизатор через конфиг (`optimizer: "heuristic" | "nlp"`).

**Пример интерфейса**:

```python
class IOptimizer(ABC):
    @abstractmethod
    def optimize(self, initial: List[RawPlacement], constraints: Constraints) -> List[FinalPlacement]: ...
```

Где `Constraints` будет содержать геометрию зоны, список падов, DRC-параметры и т.д.

---

## Этап 5. Улучшение обработки ошибок и логирования

- Использовать более структурированное логирование с контекстом (например, добавить `logger = logging.getLogger(__name__)` в каждый модуль – уже есть).
- Добавить проверки на валидность конфига при загрузке (использовать Pydantic или `__post_init__`).

---

## Итоговый план действий (по приоритетам)

1. **Этап 0** – быстрые правки (вынос функции, удаление мёртвого кода, интерфейс адаптера). (1–2 часа)
2. **Этап 1** – разделение `PlacementPlanner` на сервисы. Это центральный рефакторинг. Сделать его без изменения внешнего поведения. (4–6 часов)
3. **Этап 2** – фабрика стратегий и вынос параметров в конфиг. (1 час)
4. **Этап 3** – покрытие тестами (юнит-тесты для каждого сервиса с моками). (параллельно)
5. **Этап 4** – введение интерфейса `IOptimizer` и реализация NLP-оптимизатора как опции. (отдельная задача, может быть позже)

---

## Что НЕ нужно делать

- Не переписывать существующие алгоритмы `relax`, `find_free_point`, `strategies` – они работают и протестированы на реальной плате.
- Не менять формат YAML-конфига без необходимости (он уже используется).
- Не трогать `executor.py` и `adapter.py` глубоко, только если потребуется для интерфейсов.

---

## Как внедрять изменения безопасно

- Каждый этап делаем в отдельной ветке.
- После каждого этапа запускаем `--dry-run` на тестовой плате и сравниваем с эталоном (позиции должны совпадать).
- Постепенно добавляем тесты (сначала на новые сервисы, потом на старые через регрессионные).

---

Если вам нужны более детальные примеры кода для каждого сервиса – я могу их написать. Также можно обсудить, как именно интегрировать NLP-модель из `task.md` – когда придёт время.

Готов ответить на уточняющие вопросы.