#!/usr/bin/env python3
"""
generate_10cl006.py — генератор конфигов для 10CL006YE144C8G, один
источник данных (BANKS/CLUSTER_MAP) на три производных артефакта:

  1. rules-based конфиг (ManualSpoke) — как раньше, apply-ready,
     profiles/generated/10CL006YE144C8G.yaml.
  2. clone_placements-based конфиг (ClonePlacement) — та же геометрия,
     но через клонирование, потому что Rule/ManualSpoke не умеет
     клонировать треки (см. обсуждение в чате) —
     profiles/generated/10CL006YE144C8G.clone_placements.yaml.
     Не подключается автоматически никуда (нет include-механизма в
     конфигах) — блок clone_placements: копируется руками в
     profiles/3ch-awg-tia.yaml, когда протестировано dry-run'ом.
  3. таблица pad -> cluster (для ручной простановки поля Cluster в
     Eeschema Bulk Edit, если резолву по физической близости к якорю
     этого не хватит для каких-то спиц) — раньше отдельный скрипт
     generate_cluster_table.py, теперь слит сюда же —
     profiles/generated/10CL006YE144C8G.cluster_table.md.

ВАЖНО про шаблон: rules используют templates_file-шаблон
"cap_pair_standard" (net: null у общих via — наследует rule.net, только
Rule это умеет), clone_placements — "cap_pair_standard_clone" (net:
"{power_net}" — резолвится через params, только ClonePlacement это
умеет). Это ДВЕ РАЗНЫЕ записи в profiles/templates/3ch-awg-tia.yaml,
специально не одна — Rule не резолвит "{placeholder}" вообще, а
ClonePlacement фатально падает на via без цепи. Пока используются
параллельно (rules всё ещё боевые, clone_placements — на тест).
"""
from dataclasses import asdict
import yaml
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadspoke.config import Rule, ManualSpoke, ThermalViaArrayConfig, ClonePlacement

TEMPLATE_NAME = "cap_pair_standard"
CLONE_TEMPLATE_NAME = "cap_pair_standard_clone"

BANKS = {
    "+3V3_VCCIO": [
        ('17', 0.0, -0.5, 90.0), ('26', 0.0, -2.4, 90.0),
        ('40', 1.3, 0.0, 180.0), ('47', 2.5, 0.0, 180.0),
        ('56', 0.3, 0.0, 180.0), ('62', 2.0, 0.0, 180.0),
        ('81', 0.0, -1.0, 270.0), ('93', 0.0, 0.0, 270.0),
        ('117', -2.0, 0.0, 0.0), ('122', -1.8, 0.0, 0.0),
        ('130', 0.0, 0.0, 0.0), ('139', 0.0, 0.0, 0.0),
    ],
    "+1V2_VCCINT": [
        ('5', 0.0, 0.0, 90.0), ('29', 0.0, -1.5, 90.0),
        ('45', 1.0, 0.0, 180.0), ('61', -0.0, 0.0, 180.0),
        ('78', 0.0, 0.0, 270.0), ('102', 0.0, 1.0, 270.0),
        ('116', -0.3, 0.0, 0.0), ('134', -0.3, 0.0, 0.0),
    ],
    "+1V2_VCCD_PLL": [
        ('37', 0.5, 0.0, 180.0), ('109', -1.5, 0.0, 0.0),
    ],
    "+2V5_VCCA": [
        ('35', 0.0, -2.0, 90.0), ('107', 0.0, 1.0, 270.0),
    ],
}

# net -> cluster name (верхний уровень)
# Один кластер на весь физический экземпляр FPGA (не по банкам питания —
# банки и так разделены rule.net/params.power_net, Cluster нужен только
# чтобы отличить ЭТОТ конкретный FPGA от другого такого же на плате/
# проекте, если появится). Проставлено на плате как FPGA_PWR_BANK.
# build_cluster_table() уточняет это до FPGA_PWR_BANK/<pad> — на случай,
# если физической близости к якорю не хватит резолвнуть какую-то спицу
# (см. clone_role_resolver.py, каскад сужения).
CLUSTER_MAP = {
    "+3V3_VCCIO": "FPGA_PWR_BANK",
    "+1V2_VCCINT": "FPGA_PWR_BANK",
    "+1V2_VCCD_PLL": "FPGA_PWR_BANK",
    "+2V5_VCCA": "FPGA_PWR_BANK",
}

# --------------------- Якорь для правил/клонов (спиц) ---------------------
ANCHOR_REF = "IC1"          # используется, если USE_ANCHOR_ROLE = False
ANCHOR_ROLE = "FPGA"        # используется, если USE_ANCHOR_ROLE = True
USE_ANCHOR_ROLE = True      # переключите на False, чтобы использовать ANCHOR_REF

# --------------------- Якорь для термовиа ---------------------
THERMAL_ANCHOR_REF = "IC1"          # используется, если THERMAL_USE_ANCHOR_ROLE = False
THERMAL_ANCHOR_ROLE = "FPGA"        # используется, если THERMAL_USE_ANCHOR_ROLE = True
THERMAL_USE_ANCHOR_ROLE = True      # переключите на False, чтобы использовать THERMAL_ANCHOR_REF


def build_rules():
    rules = []
    for net, spokes_table in BANKS.items():
        cluster = CLUSTER_MAP.get(net)
        spokes = [
            ManualSpoke(
                pad=pad,
                template=TEMPLATE_NAME,
                shift_x_mm=sx,
                shift_y_mm=sy,
                rotation_deg=rot,
                cluster=cluster,
            )
            for pad, sx, sy, rot in spokes_table
        ]
        if USE_ANCHOR_ROLE:
            rules.append(Rule(net=net, spokes=spokes, anchor_role=ANCHOR_ROLE))
        else:
            rules.append(Rule(net=net, spokes=spokes, anchor_ref=ANCHOR_REF))
    return rules


def build_cluster_table():
    """[(net, pad, cluster_name), ...] в порядке BANKS (совпадает с
    порядком rules/clone_placements, для удобства сверки). cluster_name —
    FPGA_PWR_BANK/<pad>, тот же, что проставлен в anchor_cluster у
    build_clone_placements()."""
    rows = []
    for net, spokes_table in BANKS.items():
        cluster_prefix = CLUSTER_MAP[net]
        for pad, *_ in spokes_table:
            rows.append((net, pad, f"{cluster_prefix}/{pad}"))
    return rows


def build_clone_placements():
    """
    clone_placements-эквивалент build_rules() — та же геометрия
    (pad -> anchor_pad, shift -> origin, rotation), но для ClonePlacement,
    который умеет клонировать треки (Rule/ManualSpoke — нет).

    anchor_cluster проставлен ВСЕГДА (FPGA_PWR_BANK/<pad>, из
    build_cluster_table()) — это безопасно, даже пока поле Cluster в
    Eeschema ещё не размечено на конкретные пады: если ни один кандидат
    не совпадает по такому Cluster, резолвер просто пропускает эту
    ступень сужения (см. clone_role_resolver._narrow_ambiguous_candidates
    — narrow остаётся без изменений, если by_cluster пуст) и падает на
    следующую (текущее выделение, потом физическая близость к якорю).
    Поэтому можно сгенерировать и прогнать через --dry-run ДО разметки
    Cluster в схеме — если близости хватит для всех спиц, тегать вообще
    не придётся; тегать нужно только те пады, которые в dry-run упадут
    неоднозначностью.

    Требует cap_pair_standard_clone (не cap_pair_standard!) в
    profiles/templates/3ch-awg-tia.yaml — там общие via/треки на
    "{power_net}", резолвится через params ниже.
    """
    placements = []
    for net, spokes_table in BANKS.items():
        cluster_prefix = CLUSTER_MAP[net]
        net_safe = net.lstrip('+')
        for pad, sx, sy, rot in spokes_table:
            kwargs = dict(
                name=f"fpga_{net_safe}_{pad}",
                template=CLONE_TEMPLATE_NAME,
                origin_x_mm=sx,
                origin_y_mm=sy,
                rotation_deg=rot,
                anchor_pad=pad,
                params={"power_net": net},
                anchor_cluster=f"{cluster_prefix}/{pad}",
            )
            if USE_ANCHOR_ROLE:
                kwargs["anchor_role"] = ANCHOR_ROLE
            else:
                kwargs["anchor_ref"] = ANCHOR_REF
            placements.append(ClonePlacement(**kwargs))
    return placements


def write_yaml(data, path):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    print(f"Сгенерирован: {path}")


def main():
    # --- 1. rules-based конфиг (как раньше) ---
    thermal_via = ThermalViaArrayConfig(
        enabled=True,
        anchor_ref=THERMAL_ANCHOR_REF if not THERMAL_USE_ANCHOR_ROLE else None,
        anchor_role=THERMAL_ANCHOR_ROLE if THERMAL_USE_ANCHOR_ROLE else None,
        pad='145',
        net='GND',
        rows=4, cols=4, margin_mm=0.5, pattern='grid',
        drill_mm=0.3, diameter_mm=0.5,
    )

    # Этот инлайновый templates: — старый, приблизительный (руками
    # подобранные координаты, до того как появился реальный extract с
    # живой платы). Реальный, снятый с платы шаблон живёт в
    # profiles/templates/3ch-awg-tia.yaml (cap_pair_standard /
    # cap_pair_standard_clone) и используется через templates_file в
    # profiles/3ch-awg-tia.yaml — вот этот, инлайновый, оставлен только
    # для обратной совместимости с profiles/generated/10CL006YE144C8G.yaml,
    # если её кто-то ещё гоняет отдельно.
    templates = {
        "cap_pair_standard": {
            "vias": [
                {"offset_along_mm": 0.0, "offset_across_mm": 1.5,
                 "drill_mm": 0.3, "diameter_mm": 0.6},
                {"offset_along_mm": -1.0, "offset_across_mm": -5.2,
                 "drill_mm": 0.3, "diameter_mm": 0.6},
            ],
            "components": [
                {"role": "C_OUT_BYPASS", "offset_along_mm": -1.0, "offset_across_mm": 1.0,
                 "angle_deg": 270.0,
                 "vias": [{"offset_along_mm": -1.0, "offset_across_mm": 2.7,
                          "net": "GND", "drill_mm": 0.3, "diameter_mm": 0.6}]},
                {"role": "C_OUT_BULK", "offset_along_mm": -1.0, "offset_across_mm": -2.0,
                 "angle_deg": 90.0,
                 "vias": [{"offset_along_mm": -1.0, "offset_across_mm": -4.2,
                          "net": "GND", "drill_mm": 0.3, "diameter_mm": 0.6}]},
            ],
        }
    }

    rules_config = {
        "layer": "B.Cu",
        "thermal_via_array": asdict(thermal_via),
        "templates": templates,
        "rules": [asdict(r) for r in build_rules()],
    }
    write_yaml(rules_config, "profiles/generated/10CL006YE144C8G.yaml")

    # --- 2. clone_placements-based конфиг (новое) ---
    clone_config = {"clone_placements": [asdict(c) for c in build_clone_placements()]}
    write_yaml(clone_config, "profiles/generated/10CL006YE144C8G.clone_placements.yaml")

    # --- 3. таблица pad -> cluster (слито из generate_cluster_table.py) ---
    rows = build_cluster_table()
    table_path = Path("profiles/generated/10CL006YE144C8G.cluster_table.md")
    with open(table_path, "w", encoding="utf-8") as f:
        f.write("| net | pad | cluster |\n")
        f.write("|---|---|---|\n")
        for net, pad, cluster in rows:
            f.write(f"| {net} | {pad} | {cluster} |\n")
    print(f"Сгенерирован: {table_path}")
    print(f"Всего спиц: {len(rows)}")


if __name__ == "__main__":
    main()
