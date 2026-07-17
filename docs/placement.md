# `kicadspoke/placement` – Планирование и исполнение расстановки

## Назначение

Директория `placement/` содержит основную логику расстановки компонентов и создания via. Она координирует все этапы процесса:

1. **Планирование** – расчёт целевых позиций компонентов и via на основе шаблонов спиц, автоматический подбор refdes через пул ролей (`ComponentPool`) или через явное разрешение цепей для клонирования (`CloneRoleResolver`).
2. **Исполнение** – применение перемещений и создание via на плате через адаптер KiCad, с разделением на две фазы (сначала перемещения, затем via) и обязательным перечитыванием платы между ними.
3. **Логирование и откат** – сохранение информации об операции в JSON для команды `undo`.
4. **Проверка коллизий** – упрощённая проверка перекрытий (опционально).
5. **Идемпотентность** – пропуск уже существующих via и компонентов, уже стоящих на целевых позициях (через `skip_existing_components` и реестр расстановки).

Все сервисы используют адаптер `kicad/adapter.py`, геометрические утилиты `geometry/` и конфигурацию `config.py`.

---

## Структура

```
placement/
├── __init__.py                 # Экспорт публичных компонентов
├── collision.py                # Проверка коллизий компонентов (упрощённая)
├── commands.py                 # Структуры данных для команд и информации о компонентах
├── executor.py                 # Исполнитель команд (флип, перемещение, создание via)
├── planner.py                  # Главный планировщик
└── services/                   # Сервисные классы
    ├── __init__.py
    ├── clone_role_resolver.py  # Разрешение ролей для клонируемых размещений (TemplatePlacer)
    ├── component_pool.py       # Подбор компонентов по ролям и цепи (для ManualSpoke)
    ├── manual_position_calculator.py   # Расчёт позиций компонентов и via по шаблонам спиц
    └── via_planner.py          # Планирование термовиа и фильтрация существующих via
```

---

## Файлы и функции

### `__init__.py`

**Назначение:**  
Экспортирует публичные классы для удобного импорта из других модулей (например, `from kicadspoke.placement import PlacementPlanner, BatchExecutor`).  
Обычно содержит:
```python
from .planner import PlacementPlanner
from .executor import BatchExecutor
from .commands import MoveCommand, ViaCommand, PlacedComponentInfo
```

---

### `commands.py`

**Назначение:**  
Определяет структуры данных (DTO), используемые для передачи информации между компонентами системы.

**Классы:**

| Класс | Поля | Описание |
|-------|------|----------|
| `MoveCommand` | `ref: str`, `position: Vector2`, `angle: Angle`, `layer: BoardLayer` | Команда перемещения/поворота компонента. |
| `ViaCommand` | `position: Vector2`, `drill_mm: float`, `diameter_mm: float`, `net_name: str`, `owner_ref: str`, `registry_key: Optional[str]` | Команда создания переходного отверстия. `registry_key` используется для реестра расстановки (см. `registry.py`). |
| `PlacedComponentInfo` | `ref: str`, `dest: Vector2`, `angle_deg: float` | Информация о размещённом компоненте (передаётся от калькулятора к via-планировщику). В отличие от предыдущей версии, здесь нет полей для via – все via теперь вычисляются заранее в `ManualPositionCalculator`. |

**Используется в:** `planner.py`, `executor.py`, `manual_position_calculator.py`, `via_planner.py`, `registry.py`.

---

### `collision.py`

**Назначение:**  
Упрощённая проверка коллизий между компонентами (по кругам-приближениям). Использует реальные bounding box'ы через адаптер для вычисления радиусов (половина диагонали bbox).

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `compute_radii(footprints, adapter)` | Вычисляет радиусы для списка футпринтов (батч-запрос через адаптер). |
| `footprints_overlap(pos1, r1, pos2, r2, margin_mm)` | Проверяет перекрытие двух кругов с запасом. |
| `check_collisions(moves, all_footprints, adapter, ignore_refs, margin_mm)` | Проверяет коллизии между перемещаемыми компонентами и остальными. Возвращает список конфликтных пар (ref1, ref2, расстояние). |

**Используется в:** `executor.py` (опционально, при включённой проверке).  
**Примечание:** проверка может давать ложные срабатывания, поэтому отключается флагом `--no-collision-check`.

---

### `executor.py`

**Назначение:**  
Применяет команды перемещения и создания via к реальной плате через адаптер. Разделяет фазы (сначала перемещения, потом via) с обязательным перечитыванием платы между ними. Ведёт логирование для undo и поддерживает реестр расстановки.

**Класс `BatchExecutor`:**

| Метод | Описание |
|-------|----------|
| `__init__(adapter, config, batch_size)` | Инициализация с размером батча для коммитов. |
| `execute_moves(moves, check_collisions, collision_margin_mm)` | Применяет только перемещения. Возвращает список refdes, которые не удалось применить. Сохраняет исходные состояния для undo. |
| `execute_vias(vias, registry=None)` | Создаёт via. Возвращает список owner_ref'ов, для которых via не создались. Принимает опциональный `registry` для записи созданных via в реестр. **Исправлено:** теперь owner_ref в JSON-логе берётся точно для каждой via, а не приблизительно (batch[0].owner_ref). |
| `execute(moves, vias, ...)` | Обратно совместимая обёртка (не рекомендуется для боевого использования – не перечитывает плату между фазами). |
| `_needs_flip(cmd, fp_by_ref)` | Проверяет, нужно ли перевернуть компонент на целевой слой. |
| `_flip_in_batches(refs, fp_by_ref)` | Переворачивает компоненты батчами через GUI-action. |
| `_write_operation_log(move_log, via_log)` | Сохраняет JSON-лог операции для undo. |

**Важно:**  
После `execute_moves()` вызывающий код обязан выполнить `adapter.refresh_board()`, чтобы `execute_vias()` работала с актуальными данными (термовиа рассчитываются по реальному термопаду). Это реализовано в `kicadspoke_cli.py:cmd_apply()`.

**Используется в:** `kicadspoke_cli.py` для выполнения плана.

---

### `planner.py`

**Назначение:**  
Главный планировщик – координирует расчёт позиций и via (через `ManualPositionCalculator`) и применяет логику пропуска уже стоящих на месте компонентов (`skip_existing_components`). Разделяет планирование на две фазы: `plan_moves()` и `plan_vias()`.

**Класс `PlacementPlanner`:**

| Метод | Описание |
|-------|----------|
| `__init__(adapter, config)` | Инициализация, поиск целевого компонента (IC), определение слоя. |
| `_already_in_place(ref, dest, angle_deg)` | Проверяет, находится ли компонент уже на целевой позиции (с учётом слоя, позиции и угла). Допуски: 0.01 мм по координатам, 0.1° по углу. |
| `plan_moves()` | Вызывает `ManualPositionCalculator.compute_raw_positions()`, получает списки компонентов и via. Применяет `skip_existing_components` к компонентам. Возвращает список `MoveCommand`. Сохраняет `_planned` и `_planned_vias` для последующего использования в `plan_vias()`. |
| `plan_vias()` | Вызывает `ViaPlanner.plan_vias()` с сохранёнными данными. Возвращает список `ViaCommand`. Применяет `skip_existing_components` к via (через `ViaPlanner`). |
| `plan()` | Обратно совместимая обёртка (вызывает `plan_moves()` и `plan_vias()` подряд). Для боевого использования не рекомендуется (см. `kicadspoke_cli.py`). |

**Используется в:** `kicadspoke_cli.py` для получения плана.

---

### `services/`

#### `services/__init__.py`

Экспортирует публичные классы сервисов:
```python
from .component_pool import ComponentPool
from .clone_role_resolver import CloneRoleResolver
from .manual_position_calculator import ManualPositionCalculator
from .via_planner import ViaPlanner
```

---

#### `services/component_pool.py`

**Назначение:**  
Подбирает конкретные refdes компонентов для ролей шаблона. Пул строится один раз на каждое правило (`rule.net`) и разбирается спицами этого правила по очереди.

**Класс `ComponentPool`:**

| Метод | Описание |
|-------|----------|
| `__init__(adapter, net_name, roles)` | Строит пул для заданной цепи: читает все футпринты, у которых есть поле `Role` и которые подключены к `net_name`. |
| `_build()` | Внутренний метод, собирает списки refdes по ролям, сортирует их в естественном численном порядке (`C5` < `C10`). |
| `pop(role, spoke_pad)` | Забирает следующий компонент с указанной ролью. Если пул исчерпан – выбрасывает `ValidationError`. |
| `remaining_count(role)` | Возвращает количество оставшихся компонентов с данной ролью. |

**Используется в:** `manual_position_calculator.py` для получения refdes для каждой спицы.

---

#### `services/clone_role_resolver.py`

**Назначение:**  
Разрешает роли для **клонируемых размещений** (`ClonePlacement`), где сопоставление роль→ref выполняется не по выделению, а по явным цепям (через `nets` и `params`). Используется в TemplatePlacer для многократно повторяющихся секций (например, каналов ЦАП, П-фильтров).

**Класс `CloneRoleResolver`:**

| Метод | Описание |
|-------|----------|
| `__init__(adapter, placement)` | Принимает адаптер и объект `ClonePlacement`. |
| `resolve()` | Для каждой роли в шаблоне определяет цепь (через `net_resolution.resolve_net` с учётом `params` и `net_overrides`), затем ищет на плате компонент, подключённый к этой цепи и имеющий соответствующую роль. Возвращает словарь `{role: ref}`. Если компонент не найден – выбрасывает `ValidationError`. |

**Используется в:** `manual_position_calculator.py` (в будущем, для обработки `clone_placements`). Пока что в основном коде не задействован, но готов к использованию.

---

#### `services/manual_position_calculator.py`

**Назначение:**  
Расчёт абсолютных позиций компонентов и via на основе шаблонов спиц (`SpokeTemplate`) и конкретных данных спицы (`ManualSpoke`) или клонируемого размещения (`ClonePlacement`). Не использует геометрию зоны – только пад FPGA + сдвиг/поворот + локальные координаты шаблона. Подбирает refdes через `ComponentPool` (для ManualSpoke) или через `CloneRoleResolver` (для ClonePlacement). Все via (как уровня спицы, так и уровня компонента) вычисляются в этом же методе и возвращаются готовыми `ViaCommand` – без обращения к живой плате.

**Класс `ManualPositionCalculator`:**

| Метод | Описание |
|-------|----------|
| `__init__(adapter, config)` | Инициализация с адаптером и конфигом. |
| `compute_raw_positions(target_fp, rules, side)` | Для каждого правила строит `ComponentPool`, затем для каждой спицы вызывает `apply_spoke_geometry` с полученным сопоставлением роль→ref. Возвращает кортеж `(список PlacedComponentInfo, список ViaCommand)`. |
| `_resolve_clone_placements(target_fp, side)` | (в будущем) Обрабатывает `clone_placements`, используя `CloneRoleResolver`. |

**Используется в:** `planner.py`.

---

#### `services/via_planner.py`

**Назначение:**  
В текущей версии `ViaPlanner` отвечает только за:
- Фильтрацию уже существующих via (идемпотентность) для всех via, переданных из `ManualPositionCalculator` (через `skip_existing_components`).
- Планирование термовиа (массива под термопадом) с поиском свободных мест через `find_free_point`.

Все остальные via (уровня спицы и уровня компонента) уже рассчитаны в `ManualPositionCalculator` и передаются готовыми.

**Класс `ViaPlanner`:**

| Метод | Описание |
|-------|----------|
| `__init__(adapter, config)` | Инициализация. |
| `_via_already_exists(existing_vias, position, net_name)` | Проверяет, существует ли уже via с данной цепью в указанной позиции (с допуском 0.01 мм). |
| `plan_vias(planned_components, planned_vias, target_fp, target_layer)` | Основной метод: фильтрует `planned_vias` через `skip_existing_components`, строит keepout для термовиа, вызывает `_plan_thermal_vias`. Возвращает итоговый список `ViaCommand`. |
| `_build_keepout(target_fp, planned, exclude)` | Строит keepout из падов IC и компонентов (для термовиа). |
| `_plan_thermal_vias(planned, target_fp, keepout, existing_vias)` | Генерирует термовиа по сетке с поиском свободных мест и пропуском существующих. |

**Используется в:** `planner.py` (после перемещений).

---

## Взаимосвязи с другими модулями

- **`kicad/adapter.py`** – используется для всех операций с платой (чтение, запись, транзакции).
- **`geometry/spoke_layout.py`** – используется в `manual_position_calculator.py` для преобразования локальных координат в глобальные и генерации via.
- **`geometry/thermal_grid.py`** и **`geometry/keepout.py`** – используются в `via_planner.py` для термовиа и keepout.
- **`config.py`** – предоставляет структуры данных (Config, ManualSpoke, SpokeTemplate, ClonePlacement и т.д.).
- **`validation.py`** – выполняет предварительные проверки перед вызовом планировщика.
- **`registry.py`** – используется в `kicadspoke_cli.py` для управления реестром via; `executor.py` передаёт в него созданные via.
- **`net_resolution.py`** – используется в `clone_role_resolver.py` для разрешения цепей с плейсхолдерами.
- **`utils/units.py`** – константа `MM` для перевода миллиметров в нанометры.

---

## Примечания по использованию

- **Двухфазный процесс** (обязателен для корректного учёта реального состояния платы, особенно для термовиа):  
  1. Выполнить `plan_moves()` → `execute_moves()`.  
  2. Выполнить `adapter.refresh_board()`.  
  3. Выполнить `plan_vias()` → `execute_vias()`.  
  Это реализовано в `kicadspoke_cli.py:cmd_apply()`.

- **Коллизии** – проверка включена по умолчанию, но может давать ложные срабатывания. Отключается флагом `--no-collision-check`.

- **Логирование операции** – сохраняется в `logs/operation_*.json` и используется командой `undo`.

- **Dry-run** – отображает и перемещения, и via (кроме термовиа, которые могут слегка отличаться из-за keepout).

- **Идемпотентность** – включение `skip_existing_components: true` позволяет безопасно перезапускать скрипт; реестр расстановки (`registry.py`) предотвращает дублирование via.

- **Автоматический подбор refdes** – через `ComponentPool` по полю `Role` в схеме (для ManualSpoke). Для клонируемых секций используется `CloneRoleResolver` с явными цепями и плейсхолдерами.