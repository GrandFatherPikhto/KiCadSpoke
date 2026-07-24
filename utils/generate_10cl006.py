#!/usr/bin/env python3
"""
generate_10cl006.py — генератор конфига термопада/спиц для 10CL006YE144C8G.
"""
from dataclasses import asdict
import yaml
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadspoke.config import Rule, ManualSpoke, ThermalViaArrayConfig

TEMPLATE_NAME = "cap_pair_standard"

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

# net -> cluster name
CLUSTER_MAP = {
    "+3V3_VCCIO": "VCCIO_BANK",
    "+1V2_VCCINT": "VCCINT_BANK",
    "+1V2_VCCD_PLL": "PLL_BANK",
    "+2V5_VCCA": "VCCA_BANK",
}

# --------------------- Якорь для правил (спиц) ---------------------
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

def main():
    # Термовиа теперь тоже может использовать роль
    thermal_via = ThermalViaArrayConfig(
        enabled=True,
        anchor_ref=THERMAL_ANCHOR_REF if not THERMAL_USE_ANCHOR_ROLE else None,
        anchor_role=THERMAL_ANCHOR_ROLE if THERMAL_USE_ANCHOR_ROLE else None,
        pad='145',
        net='GND',
        rows=4, cols=4, margin_mm=0.5, pattern='grid',
        drill_mm=0.3, diameter_mm=0.5,
    )

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

    config = {
        "layer": "B.Cu",
        "thermal_via_array": asdict(thermal_via),
        "templates": templates,
        "rules": [asdict(r) for r in build_rules()],
    }

    output_path = "profiles/generated/10CL006YE144C8G.yaml"
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    print(f"Сгенерирован: {output_path}")

if __name__ == "__main__":
    main()