# KiCadDecapPlacer — продолжение работы (актуальный статус)

> Обновлено после разделения planner/executor на фазы. Если этот файл
> расходится с реальным кодом в папке — код важнее, это просто снимок на
> момент передачи.

## Что это за проект

Python-инструмент для автоматической расстановки развязывающих
конденсаторов (decoupling caps) вокруг BGA/TQFP-компонентов (сейчас —
FPGA 10CL006YE144C8G, TQFP144) на плате KiCad 10.0.4, через **IPC API**
(`kicad-python` / `kipy`, версия **0.7.1**).

## Архитектура (пакет `decap_placer/`)

```
decap_placer/
├── config.py              — Spoke/SpokeComponent/Rule/Config/ViaConfig/
│                             PowerViaConfig/ThermalViaArrayConfig,
│                             resolve_power_pin_facing()
├── exceptions.py
├── geometry/
│   ├── boundary.py         — ray_boundary_distance, closest_point_on_polygon
│   │                         (нормаль "наружу" — через ориентацию обхода
│   │                         полигона, НЕ через положение опрашиваемой
│   │                         точки — это была реальная, уже исправленная
│   │                         ошибка на пограничном случае)
│   ├── strategies.py       — Radial/Orthogonal/Fixed/BoundaryStrategy
│   ├── thermal_grid.py     — сетка термопереходов под термопадом
│   ├── relax.py            — relax_1d/relax_positions — раздвижка вдоль ряда
│   └── keepout.py          — Rect/build_keepout/find_free_point — ГОТОВ И
│                              ПРОТЕСТИРОВАН, НО ЕЩЁ НЕ ПОДКЛЮЧЁН к planner
├── kicad/adapter.py         — обёртка над kipy; get_footprint_pads,
│                              get_pad_by_number, get_bounding_boxes,
│                              commit_with_retry (безопасный к
│                              begin_commit(), падающему ДО присвоения commit)
├── placement/
│   ├── planner.py           — PlacementPlanner.plan_moves() / .plan_vias()
│   │                          (раздельные, plan_vias() требует, чтобы
│   │                          plan_moves() был вызван первым — иначе
│   │                          RuntimeError; .plan() — обратно совместимая
│   │                          обёртка, вызывает оба подряд без коммита)
│   ├── executor.py           — BatchExecutor.execute_moves() /
│   │                           .execute_vias() (раздельные; .execute() —
│   │                           обратно совместимая обёртка)
│   └── collision.py          — грубая проверка коллизий (fallback-радиус)
├── rules/{parser,generator}.py
└── utils/units.py

placer.py — CLI. cmd_apply УЖЕ реально коммитит между фазами:
    moves = planner.plan_moves()
    executor.execute_moves(moves, ...)
    adapter.refresh_board()        # <-- плата перечитана, пады реальные
    vias = planner.plan_vias()     # <-- вот тут и нужно вкрутить keepout
    executor.execute_vias(vias)
```

## Модель данных — "спицы" (Spoke)

Один вывод IC1 (`pad`) + список произвольного числа компонентов
(`SpokeComponent`, без своего pad — он общий на спицу). Пример в реальном
конфиге:
```yaml
spokes:
  - pad: "17"
    components:
      - {ref: "C5", placement: "inside", offset_mm: 1.0, via: true}
      - {ref: "C30", placement: "outside", offset_mm: 2.2, via: true}
```
`Spoke.power_via` (модель данных готова, логика — нет) и
`Spoke/SpokeComponent.power_pin_facing` (override с приоритетом
компонент→спица→глобальный конфиг, `resolve_power_pin_facing()` уже есть,
но повороты им ещё не пользуются).

## Реальные грабли kicad-python 0.7.1 (проверено на живом KiCad)

- Таймаут `kipy.KiCad()` по умолчанию 2000мс — мало, у нас 20000мс везде.
- Флип — ТОЛЬКО через `run_action("pcbnew.InteractiveEdit.flip")` на
  выделении, простое `footprint.layer = ...` НЕ зеркалирует площадки.
  После флипа обязательно `board.get_footprints()` заново.
- Отражение угла на B.Cu: `180° - φ`, не `φ` (эмпирически подтверждено).
- `Box2.pos`/`.size`, не `.min`/`.max`.
- `commit = None` до `try` в retry-обёртке — иначе падение самого
  `begin_commit()` превращается в `UnboundLocalError`, маскируя причину.
- Батчи по 10 — крупные транзакции роняли IPC-сессию насмерть (лечится
  только полным перезапуском KiCad, не скрипта).

## СЛЕДУЮЩИЙ шаг (буквально следующие ~20-30 строк кода)

Подключить `geometry/keepout.py` внутрь `planner.plan_vias()`:

1. После `adapter.refresh_board()` (уже происходит в `placer.py` до вызова
   `plan_vias()`) — собрать keepout-список:
   ```python
   ic1_pads = adapter.get_footprint_pads(self._target_fp)  # только те, что рядом с зоной, если нужно отфильтровать
   cap_fps = [adapter.get_footprint(c.ref) for c in ...]     # все компоненты этого прогона
   cap_pads = [p for fp in cap_fps for p in adapter.get_footprint_pads(fp)]
   bboxes = adapter.get_bounding_boxes(ic1_pads + cap_pads)
   keepout = build_keepout(bboxes, clearance_mm=0.15)  # число уточнить
   ```
2. Для каждой планируемой виа (stitching в `_plan_stitching_vias`, термо —
   отдельно) прогонять расчётную точку через
   `find_free_point(ideal, keepout, via_radius, preferred_direction=...)`
   вместо использования "сырой" точки как есть.
3. GND-виа — `preferred_direction` в сторону центра зоны по умолчанию
   (обсуждали: "внутри дворика по умолчанию").
4. Если `find_free_point` вернул `None` (место не найдено в пределах
   `max_radius_mm`) — логировать предупреждение и пропускать эту виа, не
   падать.

Дальше (не блокирует пункт выше, можно параллельно/после):
- `power_pin_facing`: для каждого компонента пробовать оба кандидатных угла
  (`base` и `base+180°`), сравнивать, где реально окажется силовой (не-GND)
  пад — эмпирически, не аналитическим выводом знака (там уже дважды
  ошибались).
- Есть отдельный документ `PDN_Spoke_Optimization_README.md` — формальная
  NLP-модель (scipy SLSQP/trust-constr) для "полировки" раскладки поверх
  уже работающей эвристики. Это отдельное, необязательное направление
  развития, не блокирует пункт про keepout выше.

## Как проверять

`python -m py_compile decap_placer/**/*.py` после каждого куска, плюс
`integration_test.py` (лежит рядом, использует моки + реальные координаты
из `.net`/`.kicad_pcb` — не требует живого KiCad для проверки геометрии).
Финальная проверка — `--dry-run`, потом уже боевой прогон на реальной плате.