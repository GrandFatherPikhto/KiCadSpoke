# Команды KiCadSpoke (CLI)

Этот документ содержит полный справочник по командам и флагам `kicadspoke_cli.py`, а также практические примеры для типовых сценариев.

---

## Базовый синтаксис

```bash
python kicadspoke_cli.py <команда> [параметры]
```

Если команда не указана, по умолчанию подразумевается `apply`.

---

## Команда `apply` – применить расстановку

Загружает конфиг, подключается к KiCad, выполняет валидацию, планирование и двухфазное исполнение (перемещения → via).

### Синтаксис

```bash
python kicadspoke_cli.py apply <путь_к_конфигу.yaml> [опции]
```

### Опции

| Флаг | Описание |
|------|----------|
| `--dry-run` | Только распечатать план (перемещения и via), не применять изменения. |
| `--timeout-ms` | Таймаут IPC-соединения с KiCad (мс). По умолчанию `20000`. |
| `--batch-size` | Количество объектов в одной транзакции. По умолчанию `10`. |
| `--verbose` | Включить подробный вывод (DEBUG). |
| `--log-file` | Сохранять логи в указанный файл. |
| `--no-collision-check` | Отключить проверку коллизий (если даёт ложные срабатывания). |
| `--collision-margin` | Дополнительный зазор при проверке коллизий (мм). По умолчанию `0.2`. |
| `--clone-placement NAME` | Обработать только один клон с указанным именем. Полезно, когда в конфиге несколько клонов в режиме «по выделению» (в KiCad активно только одно выделение). |

### Примеры

#### Стандартный запуск (расстановка компонентов и via)

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

Находит последний JSON-лог в папке `logs/` и восстанавливает состояние платы (возвращает компоненты на исходные позиции и слои, удаляет созданные via).

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

Создаёт шаблон спицы из текущего выделения в PCB-редакторе KiCad. Требуется, чтобы у каждого выделенного компонента было поле `Role`, причём роли должны быть уникальны.

### Синтаксис

```bash
python kicadspoke_cli.py extract --name <имя_шаблона> --output <файл.yaml> [--timeout-ms] [--verbose] [--log-file]
```

### Примеры

#### Извлечение шаблона в новый файл

```bash
python kicadspoke_cli.py extract --name pi_filter_vccint --output pi_filter_vccint.yaml --verbose
```

#### Добавление шаблона в существующий конфиг

```bash
python kicadspoke_cli.py extract --name my_filter --output 10CL006YE144C8G.yaml --verbose
```

Примечание: если шаблон с таким именем уже существует, он будет перезаписан.

---

## Команда `clone-extract` – снятие снимка канала (файловый клонер)

Анализирует иерархический проект (без подключения к KiCad), извлекает все компоненты, дорожки и via, принадлежащие указанному каналу, и сохраняет снимок в YAML. Полезно для изучения структуры канала перед созданием конфигурации для `ClonePlacement`.

### Синтаксис

```bash
python kicadspoke_cli.py clone-extract --net <файл.net> --pcb <файл.kicad_pcb> --channel <имя_канала> --output <файл.yaml> [--verbose]
```

### Пример

```bash
python kicadspoke_cli.py clone-extract --net my_project.net --pcb my_project.kicad_pcb --channel Channel_0 --output snapshot.yaml --verbose
```

После выполнения вы получите YAML-файл с информацией о канале, который можно использовать для написания шаблона и `ClonePlacement`.

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

1. **Перед первым запуском** выполните `extract` на существующем правильном экземпляре, чтобы получить шаблон.
2. **Проверяйте конфигурацию** через `dry-run`, чтобы убедиться, что позиции и via расставляются так, как вы ожидаете.
3. **Для отладки** используйте `--verbose` и сохраняйте лог в файл.
4. **При обработке нескольких клонов** в режиме «по выделению» используйте `--clone-placement`, чтобы обрабатывать их по одному.
5. **Если KiCad падает** при первом запуске, закройте редактор схем или сделайте интерактивную правку в PCB перед запуском (обход issue #24966).
6. **Для изучения иерархических проектов** перед написанием `ClonePlacement` используйте `clone-extract` – это даст вам точные имена цепей и refdes близнецов.

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
| `ComponentNotFoundError` | Указанный `target_ref` не найден на плате. | Проверьте refdes в конфиге. |
| `ValidationError: не хватает компонентов для ролей` | Недостаточно компонентов с полем `Role` для данной цепи. | Добавьте поле `Role` на нужные компоненты в схеме и выполните Update PCB. |
| `ConnectionError` при записи | KiCad упал (известный баг #24966) или завис. | Закройте редактор схем или сделайте интерактивную правку в PCB, затем перезапустите. |
| `Крах KiCad при первом запуске` | Открыт редактор схем и не было интерактивных правок. | Workaround: закройте схему или подвиньте компонент в PCB и сохраните. |
| `Не удаётся найти via` при undo | Via была удалена вручную. | Не страшно; undo продолжит работу для остальных объектов. |

---

## Набор актуальных комманд

### Расстановка конденсаторов питания для 10CL006Y3144C8G

```bash
python kicadspoke_cli.py .\10CL006YE144C8G.yaml --verbose --log-file logs/placer.log --verbose
```

### Отмена расстановки

```bash
python kicadspoke_cli.py undo --verbose
```

### Клонирование и применение шаблонов

### Чтение шаблона

```bash
python kicadspoke_cli.py extract --name pi_filter_vccint --output pi_filter_vccint.yaml --verbose
```

### Применение шаблона

```bash
python kicadspoke_cli.py apply .\templates\pi_filter_vccio.yaml --clone-placement pi_filter_vccio
```

### Тестирование `KiCad` на краши

#### Тест на чтение

```bash
python -m kicadspoke.diagnostics.diagnose_first_write_crash --until 8 
```

#### Тест на чтение/запись

```bash
python -m kicadspoke.diagnostics.diagnose_first_write_crash
```

#### Тест гипотезы гонки

```bash
python -m kicadspoke.diagnostics.diagnose_first_write_crash --delay 30
```

## Лицензия

Все примеры распространяются под лицензией MIT, так же как и основной проект.
