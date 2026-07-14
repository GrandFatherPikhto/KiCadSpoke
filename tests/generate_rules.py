#!/usr/bin/env python3
"""
Генератор правил для decap_placement.yaml.

Использует модуль decap_placer.rules.generator.
Пример запуска:
    python generate_rules.py --net test_boards/10CL006YE144C8G/10CL006YE144C8G.net \\
                             --pcb test_boards/10CL006YE144C8G/10CL006YE144C8G.kicad_pcb \\
                             --target IC1
"""
import argparse
import sys
from decap_placer.rules.generator import RulesGenerator

# --- Группы конденсаторов (как в исходном скрипте) ---
GROUPS = {
    "+3V3_VCCIO":      {"100nF": [f"C{i}" for i in range(5, 15)],   "4.7uF": [f"C{i}" for i in range(30, 38)]},
    "+1V2_VCCINT":     {"100nF": [f"C{i}" for i in range(19, 28)],  "4.7uF": [f"C{i}" for i in range(40, 47)]},
    "+2V5_VCCA":       {"100nF": ["C28", "C29"],                     "4.7uF": ["C51", "C52"]},
    "+1V2_VCCD_PLL":   {"100nF": ["C38", "C39"],                     "4.7uF": ["C53", "C54"]},
}

def main():
    parser = argparse.ArgumentParser(description="Генерация правил для decap_placement.yaml")
    parser.add_argument("--net", required=True, help="Путь к .net файлу")
    parser.add_argument("--pcb", required=True, help="Путь к .kicad_pcb файлу")
    parser.add_argument("--target", default="IC1", help="Refdes целевого компонента")
    parser.add_argument("--output", "-o", help="Файл для сохранения (если не указан, печатает в stdout)")
    parser.add_argument("--100nf-offset", type=float, default=1.0, help="Отступ для 100nF (inside)")
    parser.add_argument("--47uf-offset", type=float, default=2.2, help="Отступ для 4.7uF (outside)")
    parser.add_argument("--fan-step", type=float, default=0.9, help="Шаг при повторном использовании пина")
    parser.add_argument("--min-spacing", type=float, default=2.0, help="Минимальное расстояние между пинами (фильтр)")
    args = parser.parse_args()

    generator = RulesGenerator(
        net_path=args.net,
        pcb_path=args.pcb,
        target_ref=args.target,
        groups=GROUPS,
        default_100nf_offset_mm=args.nf_offset,
        default_47uf_offset_mm=args.uf_offset,
        repeat_fan_step_mm=args.fan_step,
        min_pin_spacing_mm=args.min_spacing,
    )
    yaml_str = generator.generate_yaml()
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(yaml_str)
        print(f"Правила сохранены в {args.output}")
    else:
        print(yaml_str)

if __name__ == "__main__":
    main()