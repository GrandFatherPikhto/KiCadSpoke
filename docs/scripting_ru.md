# Скриптинг: `explore` и `author`

Два аддитивных, необязательных Python-модуля для использования kicadstamp как библиотеки, в
дополнение к CLI/YAML-потоку — ничего здесь не меняет формат YAML-конфига или пайплайн
`apply`/`extract`; оба модуля — тонкие обёртки над уже существующим, дружелюбные к read-only
использованию.

## `kicadstamp.explore` — запросы только на чтение

Вырос из повторяющегося паттерна: отвечать на вопросы "у каких компонентов Role=X", "на какой
цепи этот пад", "на каком инстансе листа (`Channel_0`/`Channel_1`/...) этот футпринт" — каждый
раз новым одноразовым скриптом. `Board.select()` заменяет это одним переиспользуемым вызовом.

```python
from kicadstamp.explore import Board

board = Board.connect(schematic_dir="../test_boards/3CH-AWG-TIA")

board.select(role="AD_DAC").show()
# ref   role    cluster  sheet      nets
# ----  ------  -------  ---------  ----
# IC2   AD_DAC  -        Channel_0  ...
# IC3   AD_DAC  -        Channel_1  ...
# IC4   AD_DAC  -        Channel_2  ...

# та самая неоднозначность, что уронила apply в реальности: роль повторяется
# дважды на канал — select() показывает это сразу, а не после фатала
board.select(role="R_TERM_P", sheet="Channel_0").show()
# ref   role      cluster  sheet      nets
# ----  --------  -------  ---------  ----
# R33   R_TERM_P  -        Channel_0  ...
# R39   R_TERM_P  -        Channel_0  ...

# escape hatch: .fp — сырой FootprintInstance, для всего, что не покрыто здесь
comp = board.select(ref="IC2")[0]
comp.nets            # {'21': '/Channel_0/DAC/DAC_OUT_P', ...}
comp.fp.position      # сырой объект kipy
```

Фильтры `select()` (все опциональны, комбинируются через И):

| Фильтр | Совпадение |
|---|---|
| `ref` | точный refdes |
| `role` | точное значение поля `Role` |
| `cluster` | **по префиксу сегментов** — та же логика, что у реального резолвера `anchor_cluster` (`Channel_1` матчит `Channel_1/1V2_PLL`, но не `Channel_10`) |
| `sheet` | вхождение в цепочку резолвнутых листов-инстансов футпринта |
| `net` | любой пад на этой цепи |

`Board` — **стабильный снапшот**, снятый на `connect()`/`refresh()` — сам по себе не обновляется
никогда. Зовите `board.refresh()` после любого изменения платы (ручная правка в KiCad, или
скриптованный запуск `apply_config()`) перед тем, как доверять следующему `select()`.

## `kicadstamp.author` — кодить расстановку вместо копипаста YAML

Повторяющиеся `clone_placements` по каналам — ровно то место, где заводятся ошибки копипаста
(не тот ключ в `nets:`, задвоенный `anchor_pad:`, неверный `anchor_sheet`) — цикл `for` так
ошибиться не может. `ClonePlacement`/`Rule` (`kicadstamp.config`) — обычные dataclass, строим их
напрямую:

```python
from kicadstamp.config import ClonePlacement

clones = [
    ClonePlacement(
        name=f"channel_{i}_ad9707", role="AD_DAC",
        anchor_role="FPGA", anchor_sheet=ch,
        nets={"AD_DAC": "/Channel_{channel}/DAC/DAC_OUT_P"},
        params={"channel": i},
        origin_x_mm=0.0, origin_y_mm=25.0 - 25.0 * i, rotation_deg=270.0 - 90.0 * i,
    )
    for i, ch in enumerate(["Channel_0", "Channel_1", "Channel_2"])
]
```

**Вариант (a) — сразу в `apply`:**

```python
from kicadstamp.config import Config, load_config
from kicadstamp.author import apply_config

cfg = load_config("profiles/3ch-awg-tia.yaml")   # или Config() с нуля
cfg.clone_placements.extend(clones)

apply_config(cfg, "profiles/3ch-awg-tia.yaml", dry_run=True)
```

`config_path` (второй аргумент) — **не косметика**: если `cfg.registry_path`/
`cfg.track_registry_path` не заданы, реестры, которые делают повторные `apply` идемпотентными
(без дублей via/треков), выводятся из него (`<config_path>.registry.json` рядом с ним).
Указывайте реальный путь профиля, который расширяете, либо явно задайте
`cfg.registry_path`/`cfg.track_registry_path` — никогда не передавайте случайную заглушку.

**Вариант (b) — сгенерировать YAML** (конфиг остаётся диффуемым/ревьюабельным в git, Python
используется только на этапе авторства):

```python
from kicadstamp.author import dump_clone_placements

dump_clone_placements(clones, "profiles/subsystems/dac_channels.yaml")
```

Дальше подключаете как обычно:

```yaml
include:
  - subsystems/dac_channels.yaml
```

Варианты можно совмещать — сгенерировать YAML вариантом (b), а дальше обычные запуски `apply`
подхватят его через `include:`, без прямого вызова `apply_config()`.
