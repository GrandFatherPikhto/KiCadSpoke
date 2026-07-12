# README.md

# KiCadDecapPlacer

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**KiCadDecapPlacer** – это инструмент для автоматической расстановки развязывающих конденсаторов вокруг компонентов (FPGA, BGA, TQFP) в **KiCad** с использованием IPC-интерфейса. Программа читает конфигурационный YAML-файл, вычисляет оптимальные позиции конденсаторов относительно зоны-ограничителя и применяет изменения в открытом проекте KiCad.

## Возможности

- 🎯 Расстановка конденсаторов `inside` (между центром и выводом) и `outside` (за границей зоны)
- 🔄 Три режима ориентации: `radial`, `orthogonal`, `fixed`
- 🧷 Автоматическое создание **stitching via** на GND рядом с каждым конденсатором
- 🌡️ Генерация массива **термопереходов** (thermal via array) под термопадом (EP) – для QFN/TQFP
- 🔍 Проверка коллизий с существующими компонентами (опционально)
- 📐 Генерация правил (`rules`) из .net и .kicad_pcb файлов
- 📝 Подробное логирование (консоль + файл)
- 🧪 Режим `--dry-run` для предварительного просмотра

## Установка

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/your-username/KiCadDecapPlacer.git
   cd KiCadDecapPlacer
   ```

2. Установите зависимости (рекомендуется использовать виртуальное окружение):
   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Linux/macOS
   # или .venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   ```

   Основные зависимости:
   - [kipy](https://github.com/your-username/kipy) – обёртка над IPC KiCad
   - `pyyaml` – для чтения YAML
   - `sexpdata` – для парсинга .net и .kicad_pcb

3. Убедитесь, что KiCad запущен и открыта нужная плата.

## Использование

### Основные команды

`placer.py` имеет две подкоманды: `apply` (по умолчанию) и `generate`.

```bash
python placer.py apply <config.yaml> [options]
python placer.py generate --net <file.net> --pcb <file.kicad_pcb> [options]
```

Если подкоманда не указана, автоматически подставляется `apply`:
```bash
python placer.py decap_placement.yaml --dry-run
```

### Подкоманда `apply`

Применяет расстановку, описанную в YAML-конфиге.

**Аргументы:**

| Параметр | Описание |
|----------|----------|
| `config` | Путь к YAML-конфигу |
| `--dry-run` | Только показать план, не изменять плату |
| `--timeout-ms` | Таймаут IPC (мс), по умолчанию 20000 |
| `--batch-size` | Количество объектов в одном коммите (по умолчанию 10) |
| `--verbose` | Выводить отладочные сообщения |
| `--log-file` | Сохранять логи в файл |
| `--no-collision-check` | Отключить проверку коллизий |
| `--collision-margin` | Дополнительный зазор при проверке коллизий (мм, по умолчанию 0.2) |

### Подкоманда `generate`

Генерирует YAML-правила из файлов .net и .kicad_pcb, используя заданные группы конденсаторов.

**Аргументы:**

| Параметр | Описание |
|----------|----------|
| `--net` | Путь к .net файлу (обязательно) |
| `--pcb` | Путь к .kicad_pcb файлу (обязательно) |
| `--target` | Refdes целевого компонента (по умолчанию IC1) |
| `--output`, `-o` | Файл для сохранения (иначе печатается в stdout) |
| `--100nf-offset` | Отступ для 100nF (inside), мм |
| `--47uf-offset` | Отступ для 4.7uF (outside), мм |
| `--fan-step` | Шаг при повторном использовании пина |
| `--min-spacing` | Минимальное расстояние между пинами для фильтрации |
| `--verbose` | Подробный вывод |
| `--log-file` | Сохранять логи в файл |

### Примеры

1. **Сухой запуск** для проверки:
   ```bash
   python placer.py decap_placement.yaml --dry-run --verbose
   ```

2. **Применение с логированием в файл**:
   ```bash
   python placer.py decap_placement.yaml --log-file placer.log --batch-size 20
   ```

3. **Генерация правил**:
   ```bash
   python placer.py generate --net board.net --pcb board.kicad_pcb --output rules.yaml
   ```

## Дополнение README.md: описание YAML конфигурационного файла

Добавляем раздел **Конфигурация** после **Использование**.

---

## Конфигурация

Основной конфигурационный файл — это YAML-документ, описывающий целевую микросхему, зону-ограничитель, способ размещения и правила для каждого конденсатора.

### Структура файла

```yaml
target_ref: "IC1"                 # Refdes компонента, вокруг которого расставляем
boundary_zone: "RA_DECAP_ZONE"    # Имя Rule Area (зоны-ограничителя)
side: "back"                      # Сторона размещения: front | back
rotation_mode: "radial"           # radial | orthogonal | fixed
fixed_angle_deg: 0.0              # Угол (градусы) при rotation_mode = fixed

# Глобальные настройки stitching via (можно переопределить в assignments)
via:
  enabled: true
  net: "GND"
  drill_mm: 0.3
  diameter_mm: 0.6
  offset_from_cap_mm: 1.0
  direction: "away_from_pad"      # away_from_pad | toward_pad | perpendicular
  count: 1                        # 1 или 2 (пара по бокам)

# Массив термопереходов под термопадом (EP) – независимая секция
thermal_via_array:
  enabled: true
  target_ref: "IC1"               # По умолчанию берётся общий target_ref
  pad: "145"                      # Номер площадки термопада
  net: "GND"
  rows: 4
  cols: 4
  margin_mm: 0.5                  # Отступ от края площадки до ближайшего ряда
  pattern: "grid"                 # grid | staggered
  drill_mm: 0.3
  diameter_mm: 0.5

# Правила расстановки (список цепей с назначениями)
rules:
  - net: "+3V3_VCCIO"
    assignments:
      - ref: "C5"
        pad: "17"
        placement: "inside"       # inside | outside | boundary
        offset_mm: 1.0
        via: true                 # Можно переопределить via для данного конденсатора
      # ... другие
```

### Параметры

#### Обязательные секции

| Ключ | Тип | Описание |
|------|-----|----------|
| `target_ref` | string | Refdes целевого компонента (например, IC1). |
| `boundary_zone` | string | Имя Rule Area (зоны), которая используется как граница. Должна быть нарисована вручную на плате (F.Cu + B.Cu, is_rule_area=True). |
| `side` | string | Сторона размещения: `"front"` или `"back"`. |
| `rules` | list | Список правил для разных цепей (см. ниже). |

#### Опциональные секции

| Ключ | Тип | По умолчанию | Описание |
|------|-----|--------------|----------|
| `rotation_mode` | string | `"radial"` | Режим ориентации конденсатора: `radial` (вдоль луча к центру), `orthogonal` (округление до ближайших 90°), `fixed` (фиксированный угол). |
| `fixed_angle_deg` | float | `0.0` | Угол в градусах при `rotation_mode = fixed`. |
| `via` | dict | `{enabled: false}` | Глобальные настройки stitching via. Можно переопределить в каждом assignment. |
| `thermal_via_array` | dict | `{enabled: false}` | Массив термопереходов под термопадом. Независим от правил. |

#### Секция `via`

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `enabled` | bool | `false` | Включить создание via рядом с каждым конденсатором. |
| `net` | string | `"GND"` | Цепь, к которой подключается via. |
| `drill_mm` | float | `0.3` | Диаметр сверла (мм). |
| `diameter_mm` | float | `0.6` | Диаметр via (мм). |
| `offset_from_cap_mm` | float | `1.0` | Смещение via от точки конденсатора (мм). |
| `direction` | string | `"away_from_pad"` | Направление смещения: `away_from_pad` (от вывода), `toward_pad` (к выводу), `perpendicular` (перпендикулярно лучу). |
| `count` | int | `1` | Количество via: `1` (одна) или `2` (пара по бокам, при этом `direction` игнорируется). |

#### Секция `thermal_via_array`

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `enabled` | bool | `false` | Включить создание массива термовиа. |
| `target_ref` | string | (берётся общий) | Refdes компонента, у которого находится термопад. |
| `pad` | string | (обязательно) | Номер площадки термопада (например, `"145"`). |
| `net` | string | `"GND"` | Цепь термовиа. |
| `rows` | int | `4` | Количество рядов по вертикали. |
| `cols` | int | `4` | Количество рядов по горизонтали. |
| `margin_mm` | float | `0.5` | Отступ от края площадки до ближайшего ряда виа (мм). |
| `pattern` | string | `"grid"` | Сетка: `grid` (обычная) или `staggered` (шахматная). |
| `drill_mm` | float | `0.3` | Диаметр сверла (мм). |
| `diameter_mm` | float | `0.5` | Диаметр via (мм). |

#### Секция `rules`

Список объектов `rule`, каждый из которых имеет:

| Ключ | Тип | Описание |
|------|-----|----------|
| `net` | string | Имя цепи (должна быть на целевом компоненте). |
| `assignments` | list | Список назначений (объектов `assignment`). |

Объект `assignment`:

| Ключ | Тип | По умолчанию | Описание |
|------|-----|--------------|----------|
| `ref` | string | (обязательно) | Refdes конденсатора на плате. |
| `pad` | string | (обязательно) | Номер вывода (пина) целевого компонента, к которому подключается конденсатор. |
| `placement` | string | `"outside"` | Расположение: `inside` (между центром и выводом), `outside` (за границей зоны), `boundary` (на границе зоны). |
| `offset_mm` | float | `1.0` | Смещение относительно расчётной позиции (мм). Для `inside` – это расстояние от вывода к центру; для `outside` – отступ от границы наружу. |
| `via` | bool / dict | (наследуется) | Переопределяет глобальную секцию `via` для данного конденсатора. Может быть `true` (включить с глобальными настройками), `false` (отключить) или словарь с частичным переопределением. |

#### Пример типового файла (из тестовой платы)

```yaml
target_ref: "IC1"
boundary_zone: "RA_DECAP_ZONE"
side: "back"
rotation_mode: "radial"
via:
  enabled: true
  net: "GND"
  drill_mm: 0.3
  diameter_mm: 0.6
  offset_from_cap_mm: 1.0
  direction: "away_from_pad"
  count: 1
thermal_via_array:
  enabled: true
  pad: "145"
  net: "GND"
  rows: 4
  cols: 4
  margin_mm: 0.5
  pattern: "grid"
  drill_mm: 0.3
  diameter_mm: 0.5
rules:
  - net: "+3V3_VCCIO"
    assignments:
      - ref: "C5"
        pad: "17"
        placement: "inside"
        offset_mm: 1.0
        via: true
      - ref: "C30"
        pad: "17"
        placement: "outside"
        offset_mm: 2.2
        via: true
      # ... остальные
```

### Генерация конфига

Вместо ручного создания правил вы можете использовать подкоманду `generate`:

```bash
python placer.py generate --net board.net --pcb board.kicad_pcb --target IC1 --output rules.yaml
```

Она автоматически распределит конденсаторы по пинам на основе цепей из .net-файла.


## Архитектура проекта

Проект организован по модульному принципу. Все модули находятся в пакете `decap_placer`.

```
decap_placer/
├── __init__.py
├── config.py              # Модели данных (Config, ViaConfig, Rule, Assignment)
├── exceptions.py          # Пользовательские исключения
├── geometry/
│   ├── __init__.py
│   ├── boundary.py        # Работа с полигоном зоны (лучи, пересечения)
│   ├── strategies.py      # Стратегии размещения (Radial, Orthogonal, Fixed)
│   └── thermal_grid.py    # Расчёт сетки термовиа
├── placement/
│   ├── __init__.py
│   ├── planner.py         # Планировщик: создание команд (Move, Via) из правил
│   ├── executor.py        # Исполнитель: применение команд с батчингом и флипом
│   └── collision.py       # Проверка коллизий между компонентами
├── kicad/
│   ├── __init__.py
│   └── adapter.py         # Адаптер для взаимодействия с KiCad через kipy
├── rules/
│   ├── __init__.py
│   ├── generator.py       # Генератор правил из .net / .kicad_pcb
│   └── parser.py          # Парсеры S-выражений для .net и .kicad_pcb
└── utils/
    ├── __init__.py
    └── units.py           # Константа MM (1_000_000)
```

### Ключевые модули и классы

#### `config.py`
Определяет dataclass-модели для конфигурации. Все параметры из YAML преобразуются в типизированные объекты, что обеспечивает автодополнение и валидацию.

#### `geometry.strategies` (абстрактный класс `PlacementStrategy`)
- `RadialStrategy` – позиция вдоль луча от центра к выводу.
- `OrthogonalStrategy` – округление угла до 90° (для прямоугольных зон).
- `FixedStrategy` – фиксированный угол.

#### `placement.planner` (`PlacementPlanner`)
- Принимает адаптер KiCad и конфигурацию.
- Использует выбранную стратегию для вычисления позиций.
- Генерирует два списка команд: `MoveCommand` (перемещение + поворот) и `ViaCommand` (создание виа).

#### `placement.executor` (`BatchExecutor`)
- Разбивает команды на батчи.
- Выполняет флип (переворот на нужный слой) через GUI-action (если требуется).
- Применяет перемещения и создание виа в отдельных транзакциях с повторами при ошибках.
- Опционально проверяет коллизии перед применением.

#### `kicad.adapter` (`KiCadBoardAdapter`)
- Обёртка над `kipy`, предоставляет высокоуровневые методы: `get_footprint`, `get_zone_by_name`, `flip_selected`, `commit_with_retry` и т.д.
- Скрывает детали IPC, упрощая тестирование.

#### `rules.generator` (`RulesGenerator`)
- Читает .net и .kicad_pcb, извлекает пины и цепи.
- Группирует конденсаторы по цепям (100nF внутри, 4.7uF снаружи).
- Распределяет пины по принципу round‑robin с учётом минимального расстояния.
- Генерирует YAML-правила, совместимые с `apply`.

### Взаимодействие компонентов (схема)

```
[YAML config] → config.load_config() → Config
                                        │
                                        ▼
[KiCad] ←→ KiCadBoardAdapter ←→ PlacementPlanner
        │                               │
        │                               ▼
        │                    (moves, vias) → BatchExecutor
        │                               │
        └───────────────────────────────┘
                                        │
                                        ▼
                                Применение (флип, перемещение, виа)
```

### Расширяемость

- Добавление новой стратегии размещения – создать класс, наследующий `PlacementStrategy`, и зарегистрировать его в `_create_strategy()`.
- Поддержка новых типов виа – расширить `ViaCommand` и `_plan_stitching_vias`.
- Изменение логики генерации правил – модифицировать `RulesGenerator` (группы, отступы).
- Логирование – использует стандартный `logging`, уровень настраивается через `--verbose`.

## Обработка ошибок

Все ожидаемые ошибки (отсутствие компонента, зоны, цепи) выбрасывают `PlacerError`. В `main` они перехватываются, и выводится понятное сообщение. Неожиданные исключения логируются с полным стеком.

## Лицензия

MIT © Your Name

---

### Благодарности

- Библиотека `kipy` за IPC-доступ к KiCad.
- Сообщество KiCad за открытый API.

---
