## 📦 Модули проекта `decap_placer`

### 1. Корневые модули

| Модуль | Назначение | Ключевые элементы | Зависимости | Внешние API |
|--------|------------|-------------------|-------------|-------------|
| **`config.py`** | Модели данных и загрузка YAML-конфига | Dataclass'ы: `Config`, `Rule`, `Spoke`, `SpokeComponent`, `ViaConfig`, `ThermalViaArrayConfig`; функция `load_config()`. | Использует `yaml` (PyYAML) | Чтение файлов через `open` |
| **`exceptions.py`** | Пользовательские исключения | `PlacerError`, `BoardNotFoundError`, `ComponentNotFoundError`, `GeometryError` | Нет | Нет |
| **`placer.py`** (корневой скрипт) | CLI-точка входа: команды `apply` и `generate` | Парсер аргументов, функции `cmd_apply`, `cmd_generate`, `setup_logging`. | Всё остальное (адаптер, планировщик, исполнитель, генератор) | `argparse`, `logging`, `sys`, `pathlib` |

---

### 2. Пакет `geometry`

**Назначение:** все геометрические расчёты, не зависящие от KiCad.  
**Ответственность:** полигоны, лучи, нормали, keepout, раздвижка, стратегии размещения, тепловая сетка.

| Модуль | Что делает | Ключевые функции/классы | Использует |
|--------|------------|--------------------------|------------|
| **`boundary.py`** | Работа с границей зоны: пересечение луча, ближайшая точка на полигоне, нормаль наружу. | `ray_boundary_distance()`, `closest_point_on_polygon()`, `polygon_signed_area()` | `math`, `kipy.geometry.Vector2` |
| **`strategies.py`** | Стратегии расчёта позиции конденсатора (радиальная, ортогональная, фиксированная, по границе). | Абстрактный `PlacementStrategy`, реализации: `RadialStrategy`, `OrthogonalStrategy`, `FixedStrategy`, `BoundaryStrategy` | `boundary.py`, `math`, `Vector2` |
| **`keepout.py`** | Построение keepout-прямоугольников из bounding box'ов, поиск свободной точки для via. | `Rect`, `build_keepout()`, `point_is_clear()`, `find_free_point()` | `Vector2`; использует адаптер через `get_bounding_boxes` |
| **`relax.py`** | Раздвижка точек вдоль ряда (1D и 2D). | `relax_1d()`, `relax_positions()`, `get_tangential_axis()` | `math`, `Vector2` |
| **`thermal_grid.py`** | Генерация сетки термовиа на площадке. | `compute_thermal_via_grid()`, `get_pad_size()` | `kipy.board_types.Pad`, `math`, `Vector2` |

**Взаимодействие:**  
- `strategies` → `boundary`  
- `keepout` → адаптер для bbox  
- `relax` – независим  
- `thermal_grid` – независим  

---

### 3. Пакет `kicad`

**Назначение:** адаптер для взаимодействия с KiCad через IPC (библиотека `kipy`).  
**Ответственность:** инкапсулировать все вызовы к плате, предоставить высокоуровневый интерфейс для остальных модулей.

| Модуль | Что делает | Ключевые методы | Использует |
|--------|------------|------------------|------------|
| **`adapter.py`** | Обёртка над `kipy` для работы с платой, компонентами, зонами, цепями, транзакциями, флипом, созданием via. | `refresh_board()`, `get_footprint()`, `get_zone_by_name()`, `get_net_by_name()`, `get_bounding_boxes()`, `begin_commit()`, `commit_with_retry()`, `flip_selected()`, `create_via()` | `kipy` (gRPC), `time`, `logging` |

**Взаимодействие:**  
Используется планировщиком (`planner`), исполнителем (`executor`) и модулем коллизий (`collision`).

---

### 4. Пакет `placement`

**Назначение:** ядро логики – планирование и исполнение расстановки.

| Модуль | Что делает | Ключевые классы/функции | Зависимости |
|--------|------------|--------------------------|-------------|
| **`planner.py`** | Планировщик: вычисляет позиции и углы конденсаторов, раздвигает, планирует via с учётом keepout. | `PlacementPlanner` (методы `plan_moves`, `plan_vias`, `_resolve_facing_angle`, `_build_via_keepout`, `_plan_thermal_vias`) | `geometry.*`, `config`, `adapter` |
| **`executor.py`** | Исполнитель: применяет команды к плате (флип, перемещение, создание via) батчами, с транзакциями. | `BatchExecutor.execute()`, `_flip_in_batches()` | `adapter`, `collision`, `config` |
| **`collision.py`** | Проверка коллизий между компонентами по реальным размерам (bbox). | `check_collisions()`, `compute_radii()`, `footprints_overlap()` | `adapter`, `math` |

**Взаимодействие:**  
- `planner` → `geometry.*` и `adapter`  
- `executor` → `adapter`, `collision`  
- `collision` → `adapter`

---

### 5. Пакет `rules`

**Назначение:** генерация YAML-правил из файлов `.net` и `.kicad_pcb`.

| Модуль | Что делает | Ключевые классы/функции | Зависимости |
|--------|------------|--------------------------|-------------|
| **`parser.py`** | Парсинг S-выражений: извлекает цепи и пины из `.net`, позиции и пады из `.kicad_pcb`. | `parse_net_file()`, `parse_pcb_file()`, вспомогательные `tag`, `find_all`, `get_str` | `sexpdata`, `math` |
| **`generator.py`** | Строит объекты `Rule`/`Spoke` из спарсенных данных, распределяет конденсаторы по пинам round‑robin, генерирует YAML. | `RulesGenerator.generate()`, `generate_yaml()`, `_pins_sorted_by_angle()` | `parser`, `config`, `collections.OrderedDict` |

**Взаимодействие:**  
- `generator` → `parser`  
- `generator` → `config` (для моделей `Rule`, `Spoke`)

---

### 6. Пакет `utils`

**Назначение:** вспомогательные утилиты, общие для всех модулей.

| Модуль | Что делает | Содержимое |
|--------|------------|------------|
| **`units.py`** | Константа перевода миллиметров в нанометры. | `MM = 1_000_000` |

---

## 🔄 Общая архитектурная схема потоков данных

```
[CLI] placer.py
  │
  ├─ apply:
  │    ├─ config.py → загрузка Config
  │    ├─ kicad/adapter.py → подключение к KiCad
  │    ├─ placement/planner.py → планирование:
  │    │    ├─ geometry/strategies → вычисление позиций
  │    │    ├─ geometry/relax → раздвижка
  │    │    ├─ geometry/keepout + adapter → поиск мест для via
  │    │    └─ geometry/thermal_grid → термовиа
  │    └─ placement/executor.py → применение:
  │         ├─ kicad/adapter → флип, обновление, создание via
  │         └─ placement/collision → проверка коллизий
  │
  └─ generate:
       ├─ rules/parser.py → чтение .net и .kicad_pcb
       ├─ rules/generator.py → создание правил
       └─ config (модели) → формирование YAML
```

---

## 🔗 Внешние зависимости по модулям

| Модуль | Используемые внешние библиотеки |
|--------|--------------------------------|
| **Везде** | `logging`, `math`, `typing`, `dataclasses` |
| **config.py** | `yaml` |
| **adapter.py** | `kipy` (IPC к KiCad) |
| **parser.py** | `sexpdata` |
| **placer.py** | `argparse`, `pathlib`, `sys` |
| **geometry/*.py** | `kipy.geometry.Vector2` (только для координат) |
| **placement/*.py** | `kipy.board_types`, `kipy.geometry` |
| **tests/** | Вероятно, `unittest` или `pytest` + mock |

---

## 🧩 Степень связанности (coupling)

- **Слабосвязанные:** `geometry.*` (чистая математика, не зависят от внешнего мира)  
- **Среднесвязанные:** `rules.*` (зависят от файлов и моделей)  
- **Сильносвязанные:** `placement.*` и `kicad/adapter` (зависят от IPC, друг от друга, от конфига)  

---

## 💡 Рекомендации по улучшению модульности

- Ввести абстрактный интерфейс для адаптера (`IBoardAdapter`), чтобы `planner` и `executor` не зависели от конкретной реализации.
- Вынести функции `resolve_power_pin_facing` и `_merge_via_config` из `config.py` в `placement` или отдельный модуль.
- Разделить `PlacementPlanner` на несколько сервисов (PositionCalculator, SpacingRelaxer, KeepoutBuilder, ViaPlanner).
- Использовать внедрение зависимостей вместо создания стратегий внутри `_create_strategy`.
