# decap_placer/rules/generator.py

from typing import Dict, List, Tuple, Optional
from collections import OrderedDict
import math
from .parser import parse_net_file, parse_pcb_file
from ..config import Rule, Spoke, SpokeComponent, ViaConfig

class RulesGenerator:
    def __init__(self,
                 net_path: str,
                 pcb_path: str,
                 target_ref: str,
                 groups: Dict[str, Dict[str, List[str]]],
                 default_100nf_offset_mm: float = 1.0,
                 default_47uf_offset_mm: float = 2.2,
                 repeat_fan_step_mm: float = 0.9,
                 min_pin_spacing_mm: float = 2.0):
        """
        :param groups: {net_name: {"100nF": [refs], "4.7uF": [refs]}}

        ИЗМЕНЕНО (2026-07-12): repeat_fan_step_mm больше НЕ используется для
        разведения конденсаторов, которым не хватило своего вывода
        (round-robin повторно указал на уже занятый pin) — раньше это
        давало им РАЗНЫЙ offset_mm, что в модели "рядами вдоль зоны"
        (BoundaryStrategy) уводило их на разные ПЕРПЕНДИКУЛЯРНЫЕ линии, где
        relax_positions их уже не видел как конфликтующих (см. историю с
        C19/C27 — второй компонент того же pin'а всё равно оказывался
        в 0.9мм от первого, просто на "невидимой" для раздвижки линии).
        Теперь оба компонента получают ОДИНАКОВЫЙ base_offset и попадают в
        ОДНУ спицу (Spoke) на этом pin'е — relax_positions видит их и
        раздвигает вдоль ряда наравне со всеми остальными конфликтами.
        Параметр оставлен в сигнатуре ради обратной совместимости CLI
        (placer.py generate --fan-step), но сейчас ни на что не влияет.
        """
        self.net_path = net_path
        self.pcb_path = pcb_path
        self.target_ref = target_ref
        self.groups = groups
        self.default_100nf_offset = default_100nf_offset_mm
        self.default_47uf_offset = default_47uf_offset_mm
        self.repeat_fan_step = repeat_fan_step_mm  # не используется, см. докстринг выше
        self.min_pin_spacing = min_pin_spacing_mm

        self.net_nodes = parse_net_file(net_path)
        self.fp_info, self.pads_info = parse_pcb_file(pcb_path)

        if target_ref not in self.fp_info:
            raise ValueError(f"Целевой компонент {target_ref} не найден в PCB")
        self.target_x, self.target_y, self.target_angle = self.fp_info[target_ref]
        self.target_pads = self.pads_info.get(target_ref, {})

        # Составляем словарь pin -> net
        self.pin_net = {}
        for net_name, nodes in self.net_nodes.items():
            for ref, pin in nodes:
                if ref == target_ref:
                    self.pin_net[pin] = net_name

    def _pins_sorted_by_angle(self, net_name: str) -> List[str]:
        """Возвращает пины целевого компонента, принадлежащие цепи net_name, отсортированные по углу."""
        pins = [p for p, n in self.pin_net.items() if n == net_name and p in self.target_pads]
        def angle_of(p):
            x, y = self.target_pads[p]
            return math.atan2(y - self.target_y, x - self.target_x)
        return sorted(pins, key=angle_of)

    def _filter_pins_min_spacing(self, pins: List[str]) -> List[str]:
        """Греедивно выбирает подмножество пинов с минимальным расстоянием."""
        if len(pins) <= 1:
            return pins
        selected = [pins[0]]
        last_pos = self.target_pads[pins[0]]
        for p in pins[1:]:
            pos = self.target_pads[p]
            if math.hypot(pos[0] - last_pos[0], pos[1] - last_pos[1]) >= self.min_pin_spacing:
                selected.append(p)
                last_pos = pos
        return selected

    def generate(self) -> List[Rule]:
        """Возвращает список правил (Rule), каждое — со списком спиц (Spoke)."""
        rules = []
        for net_name, groups_dict in self.groups.items():
            pins_all = self._pins_sorted_by_angle(net_name)
            if not pins_all:
                continue  # нет пинов на этой цепи
            pins = self._filter_pins_min_spacing(pins_all)

            # Группируем компоненты по pad — ОДНА спица на pin, даже если
            # round-robin несколько раз указал на один и тот же pin.
            components_by_pad: "OrderedDict[str, List[SpokeComponent]]" = OrderedDict()

            for value_label, placement, offset_mm in [
                ("100nF", "inside", self.default_100nf_offset),
                ("4.7uF", "outside", self.default_47uf_offset)
            ]:
                caps = groups_dict.get(value_label, [])
                for i, ref in enumerate(caps):
                    pad = pins[i % len(pins)]
                    components_by_pad.setdefault(pad, []).append(
                        SpokeComponent(ref=ref, placement=placement, offset_mm=offset_mm, via=True)
                    )

            spokes = [Spoke(pad=pad, components=comps) for pad, comps in components_by_pad.items()]
            rules.append(Rule(net=net_name, spokes=spokes))
        return rules

    def generate_yaml(self) -> str:
        """Генерирует YAML-строку в формате spokes/components, совместимом с decap_placement.yaml."""
        import yaml
        rules = self.generate()
        out = {"rules": []}
        for rule in rules:
            rule_dict = {"net": rule.net, "spokes": []}
            for spoke in rule.spokes:
                spoke_dict = {"pad": spoke.pad, "components": []}
                for comp in spoke.components:
                    spoke_dict["components"].append({
                        "ref": comp.ref,
                        "placement": comp.placement,
                        "offset_mm": comp.offset_mm,
                        "via": True,
                    })
                rule_dict["spokes"].append(spoke_dict)
            out["rules"].append(rule_dict)
        return yaml.dump(out, allow_unicode=True, sort_keys=False)
