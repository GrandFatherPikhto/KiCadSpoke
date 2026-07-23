# Модули верхнего уровня KiCadSpoke (актуальная версия)

В папке `kicadspoke/` находятся основные модули, обеспечивающие загрузку конфигурации, обработку исключений, логирование, откат операций, валидацию, реестры расстановки via и треков, извлечение шаблонов и точку входа CLI. Каждый модуль решает конкретную задачу и взаимодействует с остальными через чёткие интерфейсы.

---

## 1. `kicadspoke_cli.py` — точка входа (CLI)

**Назначение:**  
Главный исполняемый скрипт, обрабатывающий аргументы командной строки, инициализирующий логирование, загружающий конфигурацию (с поддержкой `templates_file`), подключающийся к KiCad, выполняющий предварительную валидацию, запускающий планировщик и исполнитель, а также поддерживающий команды `undo`, `extract`, `clone-extract` и опциональный флаг `--clone-placement`.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `setup_logging(verbose, log_file)` | Настраивает логирование: уровень (INFO/DEBUG), вывод в консоль и/или файл. |
| `cmd_apply(args)` | Команда `apply`: загружает конфиг, подключается к KiCad, запускает валидацию, планирование и **трёхфазное** исполнение (перемещения → refresh → via → треки). Поддерживает `--dry-run` и `--clone-placement` для обработки одного клона. |
| `cmd_extract(args)` | Команда `extract`: извлекает шаблон из текущего выделения на плате (включая **треки**) и записывает его в JSON или YAML. Поддерживает `--param`, `--net-template`, `--origin-by-via-net`, `--origin-by-component-role`, а также **профили** (`--profiles`, `--profile`) для удобного переиспользования параметров. Формат определяется расширением файла; при `.json` файл записывается как плоский словарь без обёртки `templates:`. |
| `cmd_undo(args)` | Команда `undo`: находит последний JSON-лог в папке `logs/`, вызывает `undo_last_operation()` (удаляет созданные via **и треки**, восстанавливает компоненты). |
| `main()` | Парсит аргументы (поддерживает неявный `apply`), настраивает логирование, вызывает соответствующую команду, перехватывает исключения. |

**Ключевые зависимости:**  
`config.load_config`, `kicad.adapter.KiCadBoardAdapter`, `validation.run_all_checks`, `placement.planner.PlacementPlanner`, `placement.executor.BatchExecutor`, `registry.PlacementRegistry`, `registry.TrackRegistry`, `template_extraction.extract_template_from_selection`, `undo.undo_last_operation`, `cloner.extract.extract_channel`.

**Особенности:**  
- Поддерживает четыре команды: `apply`, `undo`, `extract`, `clone-extract`.
- В режиме `apply` выполняет трёхфазный процесс: перемещения → via → треки (с промежуточным перечитыванием платы).
- Флаг `--clone-placement NAME` позволяет обработать только один клон в режиме «по выделению» (полезно для отладки) или при необходимости обработать один из нескольких клонов.
- В режиме `extract` использует выделение в PCB-редакторе для создания шаблона; извлекает компоненты, via и треки. Поддерживает профили, которые хранят параметры (`name`, `output`, `param`, `net_template`, `origin_by_via_net`, `origin_by_component_role`) в отдельном YAML-файле.
- Все исключения перехватываются и логируются; пользовательские (`PlacerError`) выводятся без стека.

---

## 2. `config.py` — загрузка и хранение конфигурации

**Назначение:**  
Определяет все структуры данных (dataclass'ы), описывающие конфигурацию расстановки, и предоставляет функцию `load_config()` для загрузки YAML-файла в типизированные объекты Python. Включает проверку уникальности ролей внутри шаблона (фатальная ошибка при дублировании), поддержку **внешних файлов шаблонов** (`templates_file`), **треков** (`TemplateTrack`), а также перекрёстную валидацию `layer`/`mirror` для `ClonePlacement`.

**Основные классы (dataclass'ы):**

| Класс | Описание |
|-------|----------|
| `ThermalViaArrayConfig` | Настройки массива термовиа под термопадом (теперь с `anchor_ref` вместо `target_ref`). |
| `TemplateVia` | Описание via в шаблоне (локальные координаты, цепь, размеры). |
| `TemplateTrack` | Описание прямого отрезка дорожки в шаблоне: начальная и конечная точки (локальные), ширина, цепь, опциональный слой. |
| `TemplateComponentSlot` | Слот компонента в шаблоне: роль, локальные координаты, угол, список via, опциональный `net_template` и `layer`. |
| `SpokeTemplate` | Полный шаблон спицы: имя, список via, список треков, список слотов компонентов, абсолютный `layer`. |
| `ManualSpoke` | Конкретная спица: пад, шаблон, сдвиг, поворот, флаг `enabled`. |
| `Rule` | Правило для одной цепи: имя цепи, список спиц, `anchor_ref` (обязательное поле). |
| `ClonePlacement` | Клонируемое размещение: имя, шаблон, абсолютная точка или сдвиг от якоря, угол, словари `nets`, `params`, `net_overrides`, `layer`, `mirror`, `refs`, `by_selection`, `anchor_role`, `anchor_sheet`, `anchor_pad`. |
| `Config` | Главный объект: глобальный `layer`, шаблоны, термовиа, правила, клонирования, флаги. |

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `load_config(path)` | Читает YAML, загружает внешний файл шаблонов (`templates_file`), если указан, и объединяет с инлайновыми `templates` (инлайновые имеют приоритет). Парсит все секции, возвращает объект `Config`. Проверяет уникальность ролей в шаблонах, корректность `layer`/`mirror` в `ClonePlacement`, наличие якоря при использовании `anchor_pad`, уникальность имён и физических якорей (`anchor_ref`/`anchor_role`) среди `clone_placements`. |
| `_load_template_via(data)` | Загружает `TemplateVia`. Проверяет, что `net` — строка (защита от случайного вложения `net_overrides`). |
| `_load_template_track(data)` | Загружает `TemplateTrack`. Проверяет, что `net` — строка. |
| `_load_template_component_slot(data)` | Загружает `TemplateComponentSlot`. |
| `_load_spoke_template(name, data)` | Загружает `SpokeTemplate` с проверкой уникальности ролей. |
| `_load_manual_spoke(data)` | Загружает `ManualSpoke`. |
| `_load_clone_placement(data)` | Загружает `ClonePlacement`. Проверяет наличие `anchor_ref` для `anchor_pad`, обязательность координат при отсутствии якоря, а также взаимную исключительность `anchor_ref` и `anchor_role`. |

**Особенности:**  
- **`templates_file`** — путь к внешнему файлу шаблонов (JSON или YAML). Инлайновые `templates` дополняют/переопределяют внешние.
- Проверка уникальности ролей внутри шаблона (дублирование недопустимо).
- Поддержка `net_template` для клонирования (плейсхолдеры для цепей).
- Для `ClonePlacement` поддерживаются два режима сопоставления ролей: «по выделению» (без `nets`/`params`) и «по цепям» (с `nets` или `params`). Явный флаг `by_selection` переопределяет автоматическое определение.
- Наследование `net` для via и треков: если `net` не указан, берётся из `rule.net` (для ManualSpoke) или обязателен для ClonePlacement.
- Перекрёстная валидация `layer`/`mirror`: `mirror` без смены слоя или смена слоя без `mirror` — фатальная ошибка.
- Устаревшие поля `target_ref` и `side` в корне конфига — фатальная ошибка.

---

## 3. `exceptions.py` — иерархия исключений

**Назначение:**  
Определяет пользовательские исключения для проекта и единую функцию форматирования фатальных ошибок. Все исключения наследуются от базового `PlacerError`.

**Классы исключений:**

| Класс | Назначение |
|-------|------------|
| `PlacerError` | Базовое исключение для всех ошибок планера. |
| `BoardNotFoundError` | Не удалось получить плату из KiCad. |
| `ComponentNotFoundError` | Компонент не найден на плате. |
| `GeometryError` | Ошибка в геометрических расчётах. |
| `ValidationError` | Фатальная ошибка предварительной проверки конфигурации — программа останавливается до изменения платы. |

**Вспомогательная функция:**

| Функция | Описание |
|---------|----------|
| `format_fatal_error(title, problems)` | Форматирует список проблем в единое многострочное сообщение с рамкой из `=`. Используется как в `config.py` (проверки на этапе загрузки YAML), так и в `validation.py` (проверки после подключения к KiCad). Живёт здесь, чтобы избежать циклических импортов. |

---

## 4. `net_resolution.py` — разрешение цепей для клонируемых шаблонов

**Назначение:**  
Обеспечивает трёхслойное разрешение имени цепи для `ClonePlacement` (TemplatePlacer). Позволяет подставлять плейсхолдеры из `params` и применять переопределения `net_overrides`. Также предоставляет **обратную параметризацию** (`parametrize_net`) для `extract`.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `resolve_net(net_template, params, net_overrides)` | Принимает шаблон имени цепи (возможно с `{placeholder}`), словарь параметров для подстановки и словарь переопределений. Возвращает итоговое имя цепи. В случае отсутствия параметра для плейсхолдера бросает `ValidationError`. |
| `parametrize_net(literal_net, net_template_map, params)` | Обратная операция для `extract`: по реальному имени цепи и карте `net_template_map` восстанавливает паттерн с плейсхолдером. Проверяет round-trip (резолв паттерна с `params` должен дать исходный литерал). |

**Принцип работы `resolve_net`:**
1. Если в `net_template` нет плейсхолдеров — возвращает как есть.
2. Иначе выполняет `str.format(**params)`.
3. Затем применяет `net_overrides.get(resolved, resolved)` для точечной подмены.

**Используется в:** `placement/services/clone_role_resolver.py` (при разрешении ролей для клонированных размещений) и `geometry/clone_geometry.py` (при разрешении цепей via и треков).

---

## 5. `registry.py` — реестры расстановки via и треков

**Назначение:**  
Обеспечивает идемпотентность расстановки via и треков между прогонами. Сохраняет в JSON-файлы рядом с конфигом информацию о созданных объектах (UUID, позиция, параметры, цепь). При повторном запуске сверяет запланированные объекты с **реальными объектами на плате** (`adapter.get_vias()`, `adapter.get_tracks()`), удаляет устаревшие (prune) и создаёт только новые или изменившие параметры.

**Основные классы и функции:**

| Класс/Функция | Описание |
|---------------|----------|
| `make_registry_key(anchor_id, template_name, role, via_index)` | Генерирует составной ключ для реестра via. |
| `registry_path_for_config(config_path)` | Возвращает путь к файлу реестра via. |
| `track_registry_path_for_config(config_path)` | Возвращает путь к файлу реестра треков (отдельный файл). |
| `RegistryEntry` | Dataclass для via: UUID, позиция, цепь, параметры отверстия. |
| `TrackRegistryEntry` | Dataclass для трека: UUID, координаты начала/конца, ширина, цепь, слой. |
| `PlacementRegistry` | Класс, управляющий реестром via. |
| `TrackRegistry` | Класс, управляющий реестром треков. |
| `reconcile(planned_objects, known_anchor_ids)` | Сравнивает запланированные объекты с реестром и реальными объектами на плате, удаляет устаревшие, возвращает список объектов для реального создания. |
| `record_created(cmd, created_uuid)` | Записывает созданный объект в реестр. |

**Особенности:**
- **Сверка с живыми объектами на плате** — источник истины, а не только JSON-запись. Это предотвращает рассинхронизацию при ручном удалении или сбоях между записью в реестр и коммитом на плату.
- Ключи реестра строятся по схеме: `anchor_id|template_name|role|via_index` (для треков аналогично).
- `anchor_id` для ManualSpoke — `f"pad:{pad}"`, для ClonePlacement — `f"name:{clone.name}"` (или `anchor:{ref}:{pad}` / `role:{role}:{sheet}:{pad}` для физических якорей).
- `role` для via уровня спицы — `__spoke__`.
- Допуск на позицию: 0.01 мм.
- Поддержка `known_anchor_ids` — при использовании `--clone-placement` via/треки других клонов не удаляются (prune).
- Отдельные реестры для via и треков (разные файлы и разные структуры записей).

**Используется в:** `kicadspoke_cli.py` (при выполнении `apply`), `executor/via_executor.py` и `executor/track_executor.py`.

---

## 6. `template_extraction.py` — извлечение шаблона из выделения

**Назначение:**  
Реализует команду `extract`: из текущего выделения в PCB-редакторе KiCad извлекает шаблон спицы (компоненты, via **и треки**) и формирует структуру для записи в файл. Поддерживает параметризацию цепей через `--net-template` и выбор origin через `--origin-by-via-net` или `--origin-by-component-role`.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `extract_template_from_selection(adapter, name, params, net_template_map, origin_via_net, origin_component_role)` | Основная функция. Читает выделение (с учётом групп), фильтрует треки (только те, у которых оба конца совпадают с падами, via или другими треками из выделения), проверяет наличие и уникальность ролей, вычисляет origin (bbox или конкретный элемент), формирует список компонентов, via и треков, возвращает словарь для записи. |
| `_bbox_origin(footprints, vias)` | Вычисляет левый нижний угол bounding box'а выделения (min_x, max_y). |
| `_find_origin(...)` | Определяет origin по заданным параметрам (via_net, component_role или bbox). |
| `_filter_tracks_within_selection(...)` | Отфильтровывает треки, у которых хотя бы один конец не совпадает с чем-то ещё в выделении (защита от захвата длинных дорожек). |

**Алгоритм:**
1. Получает выделенные объекты через `adapter.get_selected_items()`.
2. Разделяет на `FootprintInstance`, `Via`, `Track`; остальное игнорируется.
3. Фильтрует треки (`_filter_tracks_within_selection`), оставляя только замкнутые в пределах выделения.
4. Проверяет наличие поля `Role` у каждого компонента и уникальность ролей.
5. Определяет origin (по `--origin-by-via-net`, `--origin-by-component-role` или bbox).
6. Для каждого компонента вычисляет `along/across` и сохраняет угол, роль и опциональный `layer`.
7. Для каждой via и трека вычисляет локальные координаты, сохраняет `net` (с параметризацией через `net_template_map`), параметры отверстия/ширины и слой.
8. Возвращает словарь `{name: {"vias": [...], "components": [...], "tracks": [...], "layer": ...}}`, готовый для записи в JSON или YAML.

**Используется в:** `kicadspoke_cli.py` (команда `extract`).

---

## 7. `undo.py` — откат последней операции

**Назначение:**  
Реализует команду `undo`, которая восстанавливает состояние платы до выполнения последней операции расстановки. Использует JSON-логи, создаваемые `executor/operation_logger.py` при каждом успешном применении изменений.

**Основная функция:**

| Функция | Описание |
|---------|----------|
| `undo_last_operation(json_path)` | Загружает JSON-лог, для каждого перемещённого компонента: определяет исходный слой (по строке), при необходимости выполняет флип, затем восстанавливает позицию и угол. Для каждой созданной via и трека удаляет их по UUID. После успешного отката удаляет JSON-файл. |

**Алгоритм восстановления слоя:**
- `original_layer` хранится в логе как строка `"F.Cu"` или `"B.Cu"`.
- Если текущий слой футпринта отличается от исходного, выполняется `adapter.flip_selected([fp])`, затем футпринт перечитывается через `adapter.get_footprint(ref)`.
- Затем восстанавливаются позиция и угол.

**Используется в:** `kicadspoke_cli.py` (команда `undo`).

---

## 8. `validation.py` — предварительные проверки конфигурации

**Назначение:**  
Выполняет фатальные проверки конфигурации **до** любых изменений на плате. Если обнаружена проблема — программа останавливается с подробным списком ошибок, не трогая плату.

**Основные функции:**

| Функция | Описание |
|---------|----------|
| `check_templates_and_pads_exist(adapter, cfg)` | Проверяет, что каждая включённая спица ссылается на существующий шаблон и существующую площадку целевого компонента (якоря). Пропускает отключённые спицы (`enabled=False`). |
| `check_role_pool_sufficiency(adapter, cfg)` | Для каждого правила строит `ComponentPool` и сверяет требуемое количество компонентов каждой роли с доступным. Если не хватает — сообщает все нехватки разом. |
| `check_clone_templates_exist(cfg)` | Проверяет, что каждый `ClonePlacement` ссылается на существующий шаблон (чисто конфиговая проверка, без подключения к KiCad). |
| `check_no_duplicate_clone_anchors(cfg)` | Проверяет уникальность имён `clone_placements` и уникальность физических якорей (комбинации `template`, `anchor_ref`, `anchor_pad` или `template`, `anchor_role`, `anchor_sheet`, `anchor_pad`) среди включённых клонов. Фатально при дублировании. |
| `check_clone_nets_exist_on_board(adapter, cfg)` | Резолвит `via.net` и `track.net` для каждого `ClonePlacement` и сверяет результат с реальными цепями платы (`adapter.get_all_nets()`). Отлавливает опечатки в `params` и `net_overrides`. |
| `check_single_selection_based_clone(cfg)` | Проверяет, что в конфиге не более одного `ClonePlacement` в режиме «по выделению» (без `nets`/`params`, или с `by_selection: true`), так как в KiCad активно только одно выделение. Подсказывает использовать `--clone-placement` для отладки. |
| `run_all_checks(adapter, cfg)` | Запускает все проверки по порядку: `check_clone_templates_exist`, `check_no_duplicate_clone_anchors`, `check_single_selection_based_clone`, `check_templates_and_pads_exist`, `check_role_pool_sufficiency`, `check_clone_nets_exist_on_board`. |

**Особенности:**  
- Сбор всех проблем: проверки собирают список ошибок, а не останавливаются на первой.
- Использование `ComponentPool`: в `check_role_pool_sufficiency` строится пул для каждой цепи.
- Для `ClonePlacement` проверяется, что не более одного клона в режиме «по выделению» (иначе фатальная ошибка).
- `check_clone_nets_exist_on_board` — проверяет, что резолвнутые цепи via и треков действительно существуют на плате.
- `check_no_duplicate_clone_anchors` — предотвращает конфликты в реестре при одинаковых физических якорях.
- Форматирование ошибок через `format_fatal_error()` из `exceptions.py`.

**Используется в:** `kicadspoke_cli.py` (перед планированием).

---

## 9. `constants.py` — глобальные константы

**Назначение:**  
Содержит глобальные константы, используемые в различных модулях проекта, что упрощает их изменение и поддержку.

| Константа | Значение | Использование |
|-----------|----------|---------------|
| `ROLE_FIELD_NAME` | `"Role"` | Имя пользовательского поля в схеме для ролей (используется в `component_pool.py`, `template_extraction.py`, `clone_role_resolver.py`). |
| `POSITION_TOLERANCE_NM` | `10_000` (0.01 мм) | Допуск по позиции для проверки «уже на месте» (используется в `planner.py`). |
| `ANGLE_TOLERANCE_DEG` | `0.1` | Допуск по углу для проверки «уже на месте» (используется в `planner.py`). |
| `POSITION_TOLERANCE_MM` | `0.01` | Допуск по позиции в миллиметрах для реестра (используется в `registry.py`). |
| `DEFAULT_BATCH_SIZE` | `10` | Размер батча по умолчанию для транзакций (используется в `executor/batch_executor.py` и `kicadspoke_cli.py`). |
| `DEFAULT_TIMEOUT_MS` | `20000` | Таймаут IPC по умолчанию (используется в `kicad/adapter.py` и `kicadspoke_cli.py`). |
| `DEFAULT_LOG_DIR` | `"logs"` | Папка для логов по умолчанию (используется в `executor/operation_logger.py`). |
| `SPOKE_LEVEL_ROLE_PLACEHOLDER` | `"__spoke__"` | Плейсхолдер для via уровня спицы в реестре (используется в `registry.py`). |

---

## Взаимосвязи модулей

```mermaid
graph TD
    CLI[kicadspoke_cli.py] --> Config[config.py]
    CLI --> Adapter[kicad/adapter.py]
    CLI --> Validation[validation.py]
    CLI --> Planner[placement/planner.py]
    CLI --> Executor[placement/executor/batch_executor.py]
    CLI --> ViaRegistry[registry.PlacementRegistry]
    CLI --> TrackRegistry[registry.TrackRegistry]
    CLI --> Extract[template_extraction.py]
    CLI --> Undo[undo.py]
    CLI --> Constants[constants.py]
    CLI --> NetResolution[net_resolution.py]

    Config --> Exceptions[exceptions.py]
    Config --> TemplatesFile[templates_file (external JSON/YAML)]

    Validation --> Config
    Validation --> ComponentPool[placement/services/component_pool.py]
    Validation --> Exceptions
    Validation --> Adapter

    ViaRegistry --> Config
    ViaRegistry --> Adapter
    ViaRegistry --> Exceptions

    TrackRegistry --> Config
    TrackRegistry --> Adapter
    TrackRegistry --> Exceptions

    Extract --> Adapter
    Extract --> Config
    Extract --> Exceptions

    Undo --> Adapter
    Undo --> Exceptions

    NetResolution --> Exceptions
    NetResolution --> Config (используется ClonePlacement)
    NetResolution --> Extract (parametrize_net)
```

Каждый модуль решает свою задачу и взаимодействует с другими через чётко определённые интерфейсы, что обеспечивает модульность и тестируемость. Благодаря централизованным константам, единому форматтеру ошибок и поддержке внешних файлов шаблонов проект легко поддерживать и расширять.
