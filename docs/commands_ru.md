# Команды KiCadSpoke (CLI)

Этот документ содержит полный справочник по командам и флагам `kicadspoke_cli.py`, а также практические примеры для типовых сценариев. Актуально для версии **v1.22.0** и выше.

---

## Базовый синтаксис

```bash
python kicadspoke_cli.py <команда> [параметры]
```

Если команда не указана, по умолчанию подразумевается `apply`.

---

## Команда `apply` – применить расстановку

Загружает конфиг, подключается к KiCad, выполняет валидацию, планирование и **трёхфазное исполнение** (перемещения → via → треки).

### Синтаксис

```bash
python kicadspoke_cli.py apply <путь_к_конфигу.yaml> [опции]
```

### Опции

| Флаг | Описание |
|------|----------|
| `--dry-run` | Только распечатать план (перемещения, via, треки), не применять изменения. |
| `--timeout-ms` | Таймаут IPC-соединения с KiCad (мс). По умолчанию `20000`. |
| `--batch-size` | Количество объектов в одной транзакции. По умолчанию `10`. |
| `--verbose` | Включить подробный вывод (DEBUG). |
| `--log-file` | Сохранять логи в указанный файл. |
| `--no-collision-check` | Отключить проверку коллизий (если даёт ложные срабатывания). |
| `--collision-margin` | Дополнительный зазор при проверке коллизий (мм). По умолчанию `0.2`. |
| `--clone-placement NAME` | Обработать только один клон с указанным именем. Полезно, когда в конфиге несколько клонов в режиме «по выделению» (в KiCad активно только одно выделение) или для отладки конкретного размещения. |

### Примеры

#### Стандартный запуск (расстановка компонентов, via и треков)

```bash
python kicadspoke_cli.py apply 10CL006YE144C8G.yaml
```

#### Запуск с подробным логированием в файл

```bash
python kicadspoke_cli.py apply 10CL006YE144C8G.yaml --verbose --log-file logs/placer.log
```

#### Предварительный просмотр (dry-run) – ничего не меняет

```bash
python kicadspoke_cli.py apply 10CL006YE144C8G.yaml --dry-run
```

#### Обработка только одного клона (например, для отладки)

```bash
python kicadspoke_cli.py apply templates\pi_filter_vccio.yaml --clone-placement pi_filter_vccio
```

#### Отключение проверки коллизий

```bash
python kicadspoke_cli.py apply 10CL006YE144C8G.yaml --no-collision-check
```

#### Увеличение таймаута для медленного KiCad

```bash
python kicadspoke_cli.py apply 10CL006YE144C8G.yaml --timeout-ms 30000
```

---

## Команда `undo` – откат последней операции

Находит последний JSON-лог в папке `logs/` и восстанавливает плату (возвращает компоненты на исходные позиции и слои, удаляет созданные via **и треки**).

### Синтаксис

```bash
python kicadspoke_cli.py undo [--verbose] [--log-file]
```

### Пример

```bash
python kicadspoke_cli.py undo --verbose
```

---

## Команда `extract` – извлечение шаблона из выделения

Создаёт шаблон спицы из текущего выделения в PCB-редакторе KiCad. Требуется, чтобы у каждого выделенного компонента было поле `Role`, причём роли должны быть уникальны. Поддерживает извлечение **треков** (дорожек) вместе с компонентами и via.

### Синтаксис

```bash
python kicadspoke_cli.py extract --name <имя_шаблона> --output <файл> [--timeout-ms] [--verbose] [--log-file] [--param KEY=VALUE] [--net-template ЛИТЕРАЛ=ПАТТЕРН] [--origin-by-via-net NET] [--origin-by-component-role ROLE] [--profiles FILE] [--profile NAME]
```

### Опции

| Флаг | Описание |
|------|----------|
| `--name` | Имя шаблона (ключ в секции `templates`). |
| `--output` | Путь к выходному файлу. Расширение определяет формат: `.json` → JSON (плоский словарь), иначе YAML. |
| `--timeout-ms` | Таймаут IPC (по умолчанию 20000 мс). |
| `--verbose` | Подробный вывод. |
| `--log-file` | Сохранять логи в файл. |
| `--param KEY=VALUE` | Задаёт параметр для проверки `--net-template` (например, `channel=1`). В шаблон не пишется, нужен только для верификации. Можно указывать несколько раз. |
| `--net-template ЛИТЕРАЛ=ПАТТЕРН` | Заменяет реальную цепь на паттерн с плейсхолдером (например, `DAC1_DB1=DAC{channel}_DB1`). Можно указывать несколько раз. |
| `--origin-by-via-net NET` | Задаёт origin шаблона по позиции via с указанной цепью (вместо левого нижнего угла bbox). Фатально, если такой via нет или она не единственна. |
| `--origin-by-component-role ROLE` | Задаёт origin по позиции компонента с указанной ролью. |
| `--profiles FILE` | YAML-файл с именованными профилями для `extract`. |
| `--profile NAME` | Использовать профиль из файла `--profiles` вместо явных флагов (нельзя сочетать с `--name`, `--output` и другими прямыми флагами). |

**Важно:** перед запуском выделите в PCB-редакторе нужные компоненты, via и треки. Роли должны быть уникальны. При сохранении в JSON файл записывается **без обёртки `templates:`**, что позволяет использовать его непосредственно как `templates_file` в основном конфиге.

### Примеры

#### Извлечение шаблона в JSON с параметризацией цепей и origin по via

```bash
python kicadspoke_cli.py extract --name pi_filter_4 --output templates/pi_filter_4.json \
  --origin-by-via-net '+3V3_VCCIO' \
  --param PWR_IN='+3V3' --param PWR_OUT='+3V3_VCCIO' \
  --net-template '+3V3_VCCIO={PWR_OUT}' --net-template '+3V3={PWR_IN}' \
  --verbose
```

#### Извлечение шаблона с использованием профиля

В файле `extract_profiles.yaml`:
```yaml
extract_profiles:
  my_filter:
    name: my_filter
    output: templates/my_filter.json
    param:
      PWR_IN: '+3V3'
      PWR_OUT: '+3V3_VCCIO'
    net_template:
      '+3V3_VCCIO': '{PWR_OUT}'
      '+3V3': '{PWR_IN}'
    origin_by_via_net: '+3V3_VCCIO'
```

Запуск:
```bash
python kicadspoke_cli.py extract --profiles extract_profiles.yaml --profile my_filter --verbose
```

#### Извлечение шаблона в YAML (без параметризации)

```bash
python kicadspoke_cli.py extract --name my_filter --output my_filter.yaml --verbose
```

#### Добавление шаблона в существующий конфиг (YAML)

```bash
python kicadspoke_cli.py extract --name my_filter --output 10CL006YE144C8G.yaml --verbose
```

Примечание: если шаблон с таким именем уже существует, он будет перезаписан.

---

## Команда `clone-extract` – снятие снимка канала (файловый клонер)

Анализирует иерархический проект (без подключения к KiCad), извлекает все компоненты, дорожки и via, принадлежащие указанному каналу, и сохраняет снимок в YAML. Полезно для изучения структуры канала перед созданием конфигурации для `ClonePlacement`.

### Синтаксис

```bash
python kicadspoke_cli.py clone-extract --net <файл.net> --pcb <файл.kicad_pcb> --channel <имя_канала> --output <файл.yaml> [--profiles FILE] [--profile NAME] [--verbose]
```

### Опции

| Флаг | Описание |
|------|----------|
| `--net` | Путь к файлу `.net` (нетлист). |
| `--pcb` | Путь к файлу `.kicad_pcb`. |
| `--channel` | Имя канала (например, `Channel_0`). |
| `--output` | Выходной YAML-файл. |
| `--profiles FILE` | YAML-файл с именованными профилями для `clone-extract`. |
| `--profile NAME` | Использовать профиль из файла `--profiles` вместо явных флагов. |
| `--verbose` | Подробный вывод. |

### Пример

```bash
python kicadspoke_cli.py clone-extract --net my_project.net --pcb my_project.kicad_pcb --channel Channel_0 --output snapshot.yaml --verbose
```

С использованием профиля (`clone_profiles.yaml`):
```yaml
clone_profiles:
  channel0:
    net: my_project.net
    pcb: my_project.kicad_pcb
    channel: Channel_0
    output: snapshot.yaml
```

Запуск:
```bash
python kicadspoke_cli.py clone-extract --profiles clone_profiles.yaml --profile channel0 --verbose
```

После выполнения вы получите YAML-файл с информацией о канале, который можно использовать для написания шаблона и `ClonePlacement`.

---

## Утилита `transform_template.py` – трансформация шаблонов (опционально)

Отдельный скрипт для постобработки уже существующих шаблонов (YAML или JSON). Позволяет поворачивать, зеркалировать и переносить начало координат без повторного извлечения с платы. Находится в папке `utils/`.

### Синтаксис

```bash
python utils/transform_template.py -i <входной_файл> -o <выходной_файл> [опции]
```

### Опции

| Флаг | Описание |
|------|----------|
| `-i, --input` | Входной YAML/JSON-файл с шаблоном. |
| `-o, --output` | Выходной файл (формат определяется расширением). |
| `--rotate DEG` | Поворот против часовой стрелки на угол (градусы). |
| `--mirror-x` | Зеркалирование по оси X (меняет знак `across`). |
| `--mirror-y` | Зеркалирование по оси Y (меняет знак `along`). |
| `--set-origin-by-via-index N` | Перенести начало координат на via с индексом N (0-based). |
| `--set-origin-by-via-net NET` | Перенести начало на via с указанной цепью. |
| `--set-origin-by-component-index N` | Перенести начало на компонент с индексом N. |
| `--set-origin-by-component-role ROLE` | Перенести начало на компонент с указанной ролью. |
| `--origin-x X --origin-y Y` | Явно задать смещение начала координат (мм). |

**Порядок применения:** сначала перенос начала (если задан), затем поворот и зеркалирование. Это гарантирует, что целевой элемент остаётся в (0,0) после всех преобразований.

### Примеры

#### Поворот на 180° и перенос начала на via с цепью

```bash
python utils/transform_template.py -i template.yaml -o template_rotated.yaml --rotate 180 --set-origin-by-via-net "GND"
```

#### Зеркалирование по X и перенос начала на компонент с ролью

```bash
python utils/transform_template.py -i template.yaml -o template_mirrored.yaml --mirror-x --set-origin-by-component-role FB
```

#### Явный сдвиг начала координат

```bash
python utils/transform_template.py -i template.yaml -o template_shifted.yaml --origin-x 1.5 --origin-y -2.0
```

---

## Диагностические команды (для отладки и тестирования)

Эти команды вызывают диагностические скрипты из папки `kicadspoke/diagnostics/`. Они помогают проверить работу IPC, геометрию, чтение полей, флип и т.д.

### Проверка чтения пользовательского поля `Role`

```bash
python -m kicadspoke.diagnostics.test_custom_fields C5 --field Role --verbose
```

### Тест перемещения одного компонента

```bash
# Сдвинуть на +1 мм по X
python -m kicadspoke.diagnostics.test_move_one_cap C5 --delta-mm 1.0

# Вернуть обратно
python -m kicadspoke.diagnostics.test_move_one_cap C5 --revert
```

### Тест флипа компонента

```bash
python -m kicadspoke.diagnostics.test_flip_one_cap C6
```

### Тест создания одной via

```bash
# Создать via рядом с C5
python -m kicadspoke.diagnostics.test_create_one_via C5 --offset-mm 1.2

# Удалить последнюю созданную via
python -m kicadspoke.diagnostics.test_create_one_via --remove
```

### Тест на краш KiCad при первой записи (issue #24966)

```bash
# Только чтения (без записи) – безопасно, если KiCad открыт
python -m kicadspoke.diagnostics.diagnose_first_write_crash --until 8

# Полный тест (чтения → запись) – может вызвать краш KiCad (используйте осторожно)
python -m kicadspoke.diagnostics.diagnose_first_write_crash

# Тест с паузой 30 секунд перед записью (проверка гипотезы о гонке)
python -m kicadspoke.diagnostics.diagnose_first_write_crash --delay 30
```

### Вывод информации о выделенных компонентах

```bash
python -m kicadspoke.diagnostics.get_selected_component
```

### Получение bounding box пада

```bash
python -m kicadspoke.diagnostics.get_pad_bbox --ref IC1 --pad 17
```

### Анализ keepout и позиций via

```bash
python -m kicadspoke.diagnostics.diagnostic_keepout 10CL006YE144C8G.yaml
```

---

## Рекомендации по использованию

1. **Перед первым запуском** выполните `extract` на существующем правильном экземпляре, чтобы получить шаблон. Используйте JSON-формат для удобного подключения через `templates_file`.
2. **Проверяйте конфигурацию** через `dry-run`, чтобы убедиться, что позиции, via и треки расставляются так, как вы ожидаете.
3. **Для отладки** используйте `--verbose` и сохраняйте лог в файл.
4. **При обработке нескольких клонов** в режиме «по выделению» используйте `--clone-placement`, чтобы обрабатывать их по одному.
5. **Если KiCad падает** при первом запуске, закройте редактор схем или сделайте интерактивную правку в PCB перед запуском (обход issue #24966).
6. **Для изучения иерархических проектов** перед написанием `ClonePlacement` используйте `clone-extract` – это даст вам точные имена цепей и refdes близнецов.
7. **Храните шаблоны отдельно** – используйте `templates_file: templates.json` в основном конфиге, чтобы избежать загромождения файла геометрией.
8. **Трансформируйте шаблоны** с помощью `transform_template.py` вместо ручного пересчёта координат.

---

## Справка по всем командам

Встроенная справка:

```bash
python kicadspoke_cli.py --help
python kicadspoke_cli.py apply --help
python kicadspoke_cli.py extract --help
python kicadspoke_cli.py undo --help
python kicadspoke_cli.py clone-extract --help
```

---

## Возможные ошибки и их решение

| Ошибка | Возможная причина | Решение |
|--------|-------------------|---------|
| `BoardNotFoundError` | KiCad не запущен или плата не открыта. | Откройте проект в KiCad и выполните `adapter.refresh_board()`. |
| `ComponentNotFoundError` | Указанный `anchor_ref` не найден на плате. | Проверьте refdes в конфиге. |
| `ValidationError: не хватает компонентов для ролей` | Недостаточно компонентов с полем `Role` для данной цепи. | Добавьте поле `Role` на нужные компоненты в схеме и выполните Update PCB. |
| `ValidationError: резолвнутая цепь via не найдена` | Опечатка в `params` или `net_overrides`. | Проверьте соответствие имён цепей в конфиге и в схеме. |
| `ConnectionError` при записи | KiCad упал (известный баг #24966) или завис. | Закройте редактор схем или сделайте интерактивную правку в PCB, затем перезапустите. |
| `Крах KiCad при первом запуске` | Открыт редактор схем и не было интерактивных правок. | Workaround: закройте схему или подвиньте компонент в PCB и сохраните. |
| `Не удаётся найти via/трек` при undo | Объект был удалён вручную. | Undo пропускает отсутствующие объекты и продолжает работу. |

---

## Набор актуальных команд (быстрый старт)

### Расстановка конденсаторов питания для FPGA

```bash
python kicadspoke_cli.py apply 10CL006YE144C8G.yaml --verbose --log-file logs/placer.log
```

### Отмена расстановки

```bash
python kicadspoke_cli.py undo --verbose
```

### Извлечение шаблона в JSON (рекомендуемый формат)

```bash
python kicadspoke_cli.py extract --name pi_filter_4 --output templates/pi_filter_4.json \
  --origin-by-via-net '+3V3_VCCIO' \
  --param PWR_IN='+3V3' --param PWR_OUT='+3V3_VCCIO' \
  --net-template '+3V3_VCCIO={PWR_OUT}' --net-template '+3V3={PWR_IN}' \
  --verbose
```

### Применение клона с внешним файлом шаблонов

```bash
python kicadspoke_cli.py apply config_with_templates_file.yaml --clone-placement fpga_filter_1v2_vccint
```

### Трансформация шаблона

```bash
python utils/transform_template.py -i templates/pi_filter_4.json -o templates/pi_filter_4_rotated.json --rotate 180 --set-origin-by-via-net '+3V3_VCCIO'
```

### Тестирование KiCad на краши

```bash
# Только чтения
python -m kicadspoke.diagnostics.diagnose_first_write_crash --until 8

# Полный тест (чтения + запись)
python -m kicadspoke.diagnostics.diagnose_first_write_crash

# С паузой 30 секунд перед записью
python -m kicadspoke.diagnostics.diagnose_first_write_crash --delay 30
```

---

## Лицензия

Все примеры распространяются под лицензией MIT, так же как и основной проект.

