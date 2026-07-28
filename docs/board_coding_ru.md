# Кодим плату: сквозной пример с `explore`/`author`

Это нарративный туториал, не справочник по API (тот — [docs/scripting_ru.md](scripting_ru.md)) —
пошаговый разбор расстановки повторяющейся 3-канальной подсистемы целиком из Python, по мотивам
реальной сессии на плате `3CH-AWG-TIA`.

## Отправная точка

На плате три одинаковых DAC-канала (`AD9707`), каждый — на своём инстансе переиспользуемого
иерархического листа (`channel.kicad_sch`, использован 3× как `Channel_0`/`Channel_1`/`Channel_2`).
Вокруг каждого DAC — небольшая группа пассивов: терминирующие резисторы (`R_TERM_P`/`R_TERM_N`),
конденсатор опоры (`C_DAC_REFIO`), резистор full-scale-adjust (`R_DAC_FS_ADJ`) и операционник
(`OP_AMP`). Писать по блоку `clone_placement` на компонент на канал руками — ровно то место, где
заводится копипаст-ошибка. В этом туториале они строятся циклом.

## Шаг 1 — сначала посмотреть

Перед тем как писать конфиг расстановки, посмотрите через `explore`, с чем реально имеете дело —
не гадайте с именами Role/Cluster/цепей и не считайте Role уникальной по умолчанию:

```python
from kicadspoke.explore import Board

board = Board.connect(config_path="profiles/3ch-awg-tia.yaml",
                       schematic_dir="../test_boards/3CH-AWG-TIA")

board.select(role="AD_DAC").show()
# ref   role    cluster  sheet          nets
# ----  ------  -------  -------------  ----
# IC2   AD_DAC  -        Channel_0/DAC  ...
# IC3   AD_DAC  -        Channel_1/DAC  ...
# IC4   AD_DAC  -        Channel_2/DAC  ...
```

Хорошо — один `AD_DAC` на канал, различимы по `sheet`. Теперь проверим терминирующий резистор:

```python
board.select(role="R_TERM_P", sheet="Channel_0").show()
# ref  role      cluster  sheet            nets
# ---  --------  -------  ---------------  ----
# R33  R_TERM_P  -        Channel_0/DAC    1=.../DAC_OUT_P, 2=.../OA_IN_P
# R39  R_TERM_P  -        Channel_0/OpAmp  1=.../OA_OUT_P, 2=.../PA_IN_P
```

Два кандидата на одном канале — одна и та же Role переиспользована для двух физически разных
задач (терминация со стороны DAC и терминация на выходе ОУ). Это ровно та неоднозначность,
которая иначе всплыла бы только фаталом посреди `apply`. Увидев это здесь, можно сразу
спроектировать обход: якориться на `AD_DAC` (уже уникален на канал) для DAC-стороны, и явным
ref/Cluster — для стороны усилителя.

## Шаг 2 — выразить повторение циклом

Как только форма подтверждена, компоненты на канал превращаются в обычный цикл Python вместо трёх
скопипащенных вручную YAML-блоков:

```python
from kicadspoke.config import ClonePlacement

channels = ["Channel_0", "Channel_1", "Channel_2"]

clones = []
for i, ch in enumerate(channels):
    clones.append(ClonePlacement(
        name=f"channel_{i}_ad9707", role="AD_DAC",
        anchor_role="FPGA", anchor_sheet=ch,
        nets={"AD_DAC": "/Channel_{channel}/DAC/DAC_OUT_P"},
        params={"channel": i},
        origin_x_mm=0.0, origin_y_mm=25.0 - 25.0 * i, rotation_deg=270.0 - 90.0 * i,
    ))
    clones.append(ClonePlacement(
        name=f"channel_{i}_r_term_p", role="R_TERM_P",
        anchor_role="AD_DAC", anchor_sheet="Channel_{channel}", anchor_pad="21",
        nets={"R_TERM_P": "/Channel_{channel}/DAC/DAC_OUT_P"},
        params={"channel": i},
        origin_x_mm=0.4, origin_y_mm=3.0, rotation_deg=270.0,
    ))
    # ... R_TERM_N, C_DAC_REFIO, R_DAC_FS_ADJ, OP_AMP — та же форма
```

Обратите внимание на `anchor_sheet="Channel_{channel}"` — `{placeholder}`, резолвится из `params`,
тот же механизм, что у `nets`/`net_template` (см. `resolve_placeholder` в
`kicadspoke/net_resolution.py`). Именно это заставляет различение из Шага 1 реально работать на
трёх итерациях цикла, а не на трёх руками вписанных именах листов.

Цикл `for` физически не может допустить ошибки, которые приходят от копипаста трёх похожих
блоков руками: не тот ключ в `nets:`, задвоенная строка `anchor_pad:`, имя листа, скопированное у
соседа — все три реально всплыли при написании именно этого конфига руками.

## Шаг 3 — попробовать

```python
from kicadspoke.config import load_config
from kicadspoke.author import apply_config

cfg = load_config("profiles/3ch-awg-tia.yaml")
cfg.clone_placements.extend(clones)

apply_config(cfg, "profiles/3ch-awg-tia.yaml", dry_run=True)
```

После реального `apply` (не `--dry-run`) снова вызовите `board.refresh()` и `board.select(...)` —
проверить результат тем же инструментом, которым искали неоднозначность на Шаге 1: цикл обратно
к `explore` закрывает вопрос "а действительно ли получилось то, что задумано", без захода в KiCad.

## Шаг 4 — решить, как это должно жить в git

Два варианта, не взаимоисключающих:

- **Оставить скриптом**, который перезапускаете при необходимости пересоздать подсистему — удобно
  пока всё ещё подбираются смещения/параметры.
- **Заморозить в YAML**, когда всё готово — тогда в поставке остаётся простой, диффуемый,
  ревьюабельный текст, а цикл на Python был лишь черновым инструментом:

  ```python
  from kicadspoke.author import dump_clone_placements
  dump_clone_placements(clones, "profiles/subsystems/dac_channels.yaml")
  ```

  ```yaml
  # profiles/3ch-awg-tia.yaml
  include:
    - subsystems/dac_channels.yaml
  ```

## Одна вещь, которую легко упустить

`apply_config(cfg, config_path, ...)` — `config_path` определяет, где живут via/track-реестры
(`registry_path_for_config()` в `kicadspoke/registry.py`, если `cfg.registry_path` не задан явно).
Передавайте ОДИН И ТОТ ЖЕ `config_path` при каждом перезапуске одного и того же скрипта, иначе
реестр примет каждый прогон за новую плату и начнёт плодить дубликаты via/треков вместо сверки с
уже существующими — подробнее в [docs/scripting_ru.md](scripting_ru.md).
