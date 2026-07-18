# `kicadspoke/placement` – Планирование и исполнение расстановки

## Назначение

Директория `placement/` содержит основную бизнес-логику расстановки компонентов и создания via. Она координирует все этапы процесса:

1. **Планирование** – расчёт целевых позиций компонентов и via на основе шаблонов спиц для двух типов размещений:
   - **`ManualSpoke`** (правила `rules`) – привязка к падам целевого компонента (IC) с автоматическим подбором refdes через пул ролей (`ComponentPool`).
   - **`ClonePlacement`** (клонируемые секции) – многократное применение шаблона в разных местах платы с разрешением ролей по выделению или по явным цепям (`CloneRoleResolver`).
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
├── planner.py                  # Главный планировщик
├── interfaces.py               # Интерфейсы IPositionCalculator и IViaPlanner
├── executor/                   # Исполнитель команд (разбит на модули)
│   ├── __init__.py
│   ├── base.py                 # Утилиты (layer_to_str)
│   ├── batch_executor.py       # Фасад, объединяющий перемещения и via
│   ├── move_executor.py        # Исполнение перемещений
│   ├── via_executor.py         # Исполнение создания via
│   ├── flip_manager.py         # Управление флипом компонентов
│   └── operation_logger.py     # Логирование операций в JSON
└── services/                   # Сервисные классы
    ├── __init__.py
    ├── component_pool.py       # Подбор компонентов по ролям и цепи (для ManualSpoke)
    ├── clone_role_resolver.py  # Разрешение ролей для ClonePlacement (по выделению или по цепям)
    ├── clone_position_calculator.py # Расчёт позиций и via для ClonePlacement
    ├── manual_position_calculator.py   # Расчёт позиций и via для ManualSpoke
    └── via_planner.py          # Планирование термовиа и фильтрация существующих via
```

---

## Подробное описание файлов и модулей

### `commands.py` – DTO для команд

Определяет структуры данных, используемые для передачи информации между компонентами.

| Класс | Поля | Описание |
|-------|------|----------|
| `MoveCommand` | `ref`, `position`, `angle`, `layer` | Команда перемещения/поворота компонента. |
| `ViaCommand` | `position`, `drill_mm`, `diameter_mm`, `net_name`, `owner_ref`, `registry_key` | Команда создания переходного отверстия. `registry_key` используется реестром для идемпотентности (см. `registry.py`). |
| `PlacedComponentInfo` | `ref`, `dest`, `angle_deg` | Информация о размещённом компоненте (передаётся от калькулятора к via-планировщику). |

**Используется в:** `planner.py`, `executor/`, `services/`.

---

### `collision.py` – Проверка коллизий

Упрощённая проверка коллизий между компонентами (по кругам-приближениям). Использует реальные bounding box'ы через адаптер для вычисления радиусов (половина диагонали bbox).

| Функция | Описание |
|---------|----------|
| `compute_radii(footprints, adapter)` | Вычисляет радиусы для списка футпринтов (батч-запрос через адаптер). |
| `footprints_overlap(pos1, r1, pos2, r2, margin_mm)` | Проверяет перекрытие двух кругов с запасом. |
| `check_collisions(moves, all_footprints, adapter, ignore_refs, margin_mm)` | Проверяет коллизии между перемещаемыми компонентами и остальными. Возвращает список конфликтных пар (ref1, ref2, расстояние). |

**Используется в:** `executor/move_executor.py` (опционально, при включённой проверке).  
**Примечание:** проверка может давать ложные срабатывания, поэтому отключается флагом `--no-collision-check`.

---

### `interfaces.py` – Абстрактные интерфейсы

Определяет интерфейсы для калькуляторов позиций и планировщиков via, что позволяет легко подменять реализации (например, для автоматической геометрии в будущем) и улучшает тестируемость.

| Интерфейс | Метод | Описание |
|-----------|-------|----------|
| `IPositionCalculator` | `compute_raw_positions(target_fp, rules, side)` | Расчёт позиций компонентов и via для `ManualSpoke` (на основе падов IC). |
| `IViaPlanner` | `plan_vias(planned_components, planned_vias, target_fp, target_layer)` | Планирование via (термовиа + фильтрация через реестр). |

**Используются в:** `planner.py`, `manual_position_calculator.py`, `via_planner.py`.

---

### `planner.py` – Главный планировщик (`PlacementPlanner`)

Координирует расчёт позиций и via для `rules` (через `ManualPositionCalculator`) и `clone_placements` (через `ClonePositionCalculator`). Применяет логику пропуска уже стоящих на месте компонентов (`skip_existing_components`). Разделяет планирование на две фазы: `plan_moves()` и `plan_vias()`.

| Метод | Описание |
|-------|----------|
| `__init__(adapter, config)` | Инициализация, поиск целевого компонента (IC), определение слоя. |
| `_already_in_place(ref, dest, angle_deg)` | Проверяет, находится ли компонент уже на целевой позиции (с учётом слоя, позиции и угла). Допуски: 0.01 мм по координатам, 0.1° по углу. |
| `plan_moves()` | Вызывает `ManualPositionCalculator.compute_raw_positions()` для `rules` и `ClonePositionCalculator.compute_raw_positions()` для `clone_placements`, объединяет результаты. Применяет `skip_existing_components` к компонентам. Возвращает список `MoveCommand`. Сохраняет `_planned` и `_planned_vias` для последующего использования в `plan_vias()`. |
| `plan_vias()` | Вызывает `ViaPlanner.plan_vias()` с сохранёнными данными. Возвращает список `ViaCommand`. Применяет `skip_existing_components` к via (через `ViaPlanner` и реестр). |
| `plan()` | Обратно совместимая обёртка (вызывает `plan_moves()` и `plan_vias()` подряд). Для боевого использования не рекомендуется (см. `kicadspoke_cli.py`). |

**Используется в:** `kicadspoke_cli.py` для получения плана.

---

### `executor/` – Исполнители команд

Директория `executor/` разбита на несколько модулей для улучшения читаемости и тестируемости.

#### `base.py` – общие утилиты
`layer_to_str(layer)` преобразует `BoardLayer` в строку `"F.Cu"` или `"B.Cu"`.

#### `operation_logger.py` – логирование операций
Класс `OperationLogger` записывает JSON-файлы в папку `logs/` для команды `undo`. Метод `write_operation_log(move_log, via_log)` создаёт файл с временной меткой.

#### `flip_manager.py` – управление флипом
Класс `FlipManager` проверяет, какие компоненты требуют флипа (слой отличается от целевого), выполняет флип батчами через `adapter.flip_selected` и возвращает обновлённый словарь `ref->footprint`.

#### `move_executor.py` – исполнение перемещений
Класс `MoveExecutor` выполняет перемещения, включая проверку коллизий, флип и батчирование. Метод `execute_moves(moves, check_collisions, collision_margin_mm)` возвращает `(failed_refs, move_log)`.

#### `via_executor.py` – создание via
Класс `ViaExecutor` создаёт via на плате, используя реестр расстановки (если передан) для записи созданных via. Метод `execute_vias(vias, registry)` возвращает `(failed_via_owners, via_log)`.

#### `batch_executor.py` – фасад
Класс `BatchExecutor` объединяет `MoveExecutor` и `ViaExecutor`, управляет логированием. Сохраняет совместимость со старым интерфейсом. Методы `execute_moves()` и `execute_vias()` вызывают соответствующие исполнители, а затем `OperationLogger` записывает единый JSON-лог.

---

### `services/` – Сервисные классы

#### `component_pool.py` – пул компонентов для ManualSpoke
Класс `ComponentPool` подбирает конкретные refdes для ролей шаблона в рамках `ManualSpoke`. Пул строится один раз на каждое правило (`rule.net`) и разбирается спицами этого правила по очереди.

| Метод | Описание |
|-------|----------|
| `__init__(adapter, net_name, roles)` | Строит пул: читает все футпринты с полем `Role`, подключённые к `net_name`, сортирует их по естественному числовому порядку (`C5` < `C10`). |
| `pop(role, spoke_pad)` | Забирает следующий компонент с указанной ролью. Если пул исчерпан – выбрасывает `ValidationError`. |
| `remaining_count(role)` | Возвращает количество оставшихся компонентов с данной ролью. |

**Используется в:** `manual_position_calculator.py`.

#### `clone_role_resolver.py` – разрешение ролей для ClonePlacement
Поддерживает два режима:

- **Режим «по выделению»** – пользователь выделяет компоненты конкретного экземпляра в PCB-редакторе. Программа считывает поле `Role` у каждого выделенного компонента и сопоставляет с ролями шаблона.
- **Режим «по цепям»** – для каждого слота роли явно задаётся цепь (через `nets` или `net_template` с плейсхолдерами). Программа ищет компоненты с нужной ролью, подключённые к этой цепи, разрешая имена цепей через `net_resolution` (с поддержкой `params` и `net_overrides`).

Функции:
- `clone_uses_selection_mode(clone)` – определяет режим по наличию `nets` или `params`.
- `resolve_roles_by_selection(adapter, template, clone_name)` – сопоставление по выделению.
- `resolve_roles_by_nets(adapter, template, clone)` – сопоставление по цепям.

**Используется в:** `clone_position_calculator.py`.

#### `clone_position_calculator.py` – расчёт для ClonePlacement
Класс `ClonePositionCalculator` рассчитывает абсолютные позиции компонентов и via для `ClonePlacement`. Не требует `target_fp` или `rules` – использует абсолютные координаты `origin_x_mm/origin_y_mm` (или сдвиг от якоря) и разрешает роли через `clone_role_resolver`.

Метод `compute_raw_positions(clone_placements)` для каждого клона определяет режим, получает `role_to_ref`, вызывает `apply_clone_geometry` и возвращает `(PlacedComponentInfo[], ViaCommand[])` с корректными `registry_key` (anchor_id = `name:{clone.name}`).

**Используется в:** `planner.py`.

#### `manual_position_calculator.py` – расчёт для ManualSpoke
Реализует интерфейс `IPositionCalculator`. Класс `ManualPositionCalculator` рассчитывает абсолютные позиции компонентов и via для `ManualSpoke` на основе падов IC, шаблонов и пула ролей.

Метод `compute_raw_positions(target_fp, rules, side)` для каждого правила строит `ComponentPool`, затем для каждой спицы вызывает `apply_spoke_geometry` с полученным сопоставлением роль→ref. Возвращает кортеж `(список PlacedComponentInfo, список ViaCommand)` с корректными `registry_key` (anchor_id = `pad:{pad}`).

**Используется в:** `planner.py`.

#### `via_planner.py` – планирование via и термовиа
Реализует интерфейс `IViaPlanner`. Отвечает только за:
- Фильтрацию уже существующих via (идемпотентность) для всех via, переданных из калькуляторов (через `skip_existing_components` и реестр).
- Планирование термовиа (массива под термопадом) с поиском свободных мест через `find_free_point`.

Все остальные via (уровня спицы и уровня компонента) уже рассчитаны в `ManualPositionCalculator` и `ClonePositionCalculator` и передаются готовыми.

| Метод | Описание |
|-------|----------|
| `__init__(adapter, config)` | Инициализация. |
| `_via_already_exists(existing_vias, position, net_name)` | Проверяет, существует ли уже via с данной цепью в указанной позиции (с допуском 0.01 мм). |
| `plan_vias(planned_components, planned_vias, target_fp, target_layer)` | Основной метод: фильтрует `planned_vias` через `skip_existing_components` и реестр, строит keepout для термовиа, вызывает `_plan_thermal_vias`. Возвращает итоговый список `ViaCommand`. |
| `_build_keepout(target_fp, planned, exclude)` | Строит keepout из падов IC и компонентов (для термовиа). |
| `_plan_thermal_vias(planned, target_fp, keepout, existing_vias)` | Генерирует термовиа по сетке с поиском свободных мест и пропуском существующих. |

**Используется в:** `planner.py` (после перемещений).

---

## Взаимосвязи с другими модулями

- **`kicad/adapter.py`** – используется для всех операций с платой (чтение, запись, транзакции).
- **`geometry/spoke_layout.py`** – используется в `manual_position_calculator.py` для преобразования локальных координат в глобальные и генерации via.
- **`geometry/clone_geometry.py`** – используется в `clone_position_calculator.py` для аналогичного преобразования для `ClonePlacement`.
- **`geometry/thermal_grid.py`** и **`geometry/keepout.py`** – используются в `via_planner.py` для термовиа и keepout.
- **`config.py`** – предоставляет структуры данных (Config, ManualSpoke, SpokeTemplate, ClonePlacement и т.д.).
- **`validation.py`** – выполняет предварительные проверки перед вызовом планировщика.
- **`registry.py`** – используется в `kicadspoke_cli.py` для управления реестром via; `executor/via_executor.py` передаёт в него созданные via.
- **`net_resolution.py`** – используется в `clone_role_resolver.py` для разрешения цепей с плейсхолдерами.
- **`constants.py`** – константы (допуски, имена полей, таймауты) используются в `planner.py`, `component_pool.py`, `via_planner.py` и других модулях.
- **`utils/units.py`** – константа `MM` для перевода миллиметров в нанометры.

---

## Рекомендации по использованию

### Двухфазный процесс (обязателен для корректного учёта реального состояния платы)
1. Выполнить `plan_moves()` → `execute_moves()`.
2. Выполнить `adapter.refresh_board()` (перечитать плату после перемещений).
3. Выполнить `plan_vias()` → `execute_vias()`.

Это реализовано в `kicadspoke_cli.py:cmd_apply()`.

### Идемпотентность
- Включите `skip_existing_components: true` в корне конфига, чтобы пропускать уже стоящие на месте компоненты и via.
- Реестр расстановки (`registry.py`) автоматически предотвращает дублирование via.

### Клонирование секций
- Для штучных экземпляров (одна MCU) используйте режим «по выделению» – не задавайте `nets` и `params`, выделите компоненты в KiCad перед запуском.
- Для многократно повторяющихся блоков (П-фильтры, каналы) задайте `nets` и `params` – программа разрешит роли по цепям, и вы сможете обработать несколько клонов за один прогон.

### Коллизии
- Проверка включена по умолчанию, но может давать ложные срабатывания – отключайте флагом `--no-collision-check`.

### Откат
- Логи операций сохраняются в `logs/operation_*.json` и используются командой `undo`.

---

## Примечания для разработчиков

- При добавлении нового типа размещения (например, автоматического по зоне) реализуйте соответствующий калькулятор, наследующий `IPositionCalculator`, и зарегистрируйте его в `PlacementPlanner`.
- Все сервисы должны получать адаптер и конфиг через конструктор (внедрение зависимости).
- Для тестирования используйте мок-адаптер, реализующий `IBoardAdapter`.

---

## Лицензия

Все модули `placement/` распространяются под лицензией MIT, так же как и основной проект.