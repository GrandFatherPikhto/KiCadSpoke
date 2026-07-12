import sexpdata
from sexpdata import Symbol
import math

NET_PATH = "test_boards/10CL006YE144C8G/10CL006YE144C8G.net"
PCB_PATH = "test_boards/10CL006YE144C8G/10CL006YE144C8G.kicad_pcb"

def tag(item):
    return str(item[0]) if isinstance(item, list) and item and isinstance(item[0], Symbol) else None
def find_all(sexp, t):
    return [i for i in sexp if isinstance(i, list) and tag(i) == t]
def find_first(sexp, t):
    r = find_all(sexp, t); return r[0] if r else None
def get_str(sexp, t):
    for item in sexp:
        if isinstance(item, list) and tag(item) == t:
            for v in item[1:]:
                if isinstance(v, str): return v
                if isinstance(v, Symbol): return str(v)
    return None

with open(NET_PATH, encoding="utf-8") as f:
    net_data = sexpdata.load(f)
nets_root = find_first(net_data, "nets")
net_nodes = {}
for net in find_all(nets_root, "net"):
    name = get_str(net, "name")
    net_nodes[name] = [(get_str(n, "ref"), get_str(n, "pin")) for n in find_all(net, "node")]

with open(PCB_PATH, encoding="utf-8") as f:
    pcb = sexpdata.load(f)

def get_reference(fp_node):
    for prop in find_all(fp_node, "property"):
        if len(prop) > 2 and prop[1] == "Reference":
            return prop[2]
    return None

def rotate(x, y, angle_deg):
    r = math.radians(angle_deg)
    return x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r)

footprints = find_all(pcb, "footprint")
fp_node_by_ref = {}
for fp in footprints:
    ref = get_reference(fp)
    if ref:
        fp_node_by_ref[ref] = fp

at = find_first(fp_node_by_ref["IC1"], "at")
ic1_x, ic1_y = float(at[1]), float(at[2])
ic1_angle = float(at[3]) if len(at) > 3 else 0.0

ic1_pads_abs = {}
for pad in find_all(fp_node_by_ref["IC1"], "pad"):
    pad_number = str(pad[1])
    pat = find_first(pad, "at")
    lx, ly = float(pat[1]), float(pat[2])
    rx, ry = rotate(lx, ly, ic1_angle)
    ic1_pads_abs[pad_number] = (ic1_x + rx, ic1_y + ry)

ic1_pin_net = {}
for name, nodes in net_nodes.items():
    for ref, pin in nodes:
        if ref == "IC1":
            ic1_pin_net[pin] = name

def pins_sorted_by_angle(net):
    pins = [p for p, n in ic1_pin_net.items() if n == net and p in ic1_pads_abs]
    def angle_of(p):
        x, y = ic1_pads_abs[p]
        return math.atan2(y - ic1_y, x - ic1_x)
    return sorted(pins, key=angle_of)

# --- Финальные группы, подтверждённые пользователем ---
GROUPS = {
    "+3V3_VCCIO":      {"100nF": [f"C{i}" for i in range(5, 15)],   "4.7uF": [f"C{i}" for i in range(30, 38)]},
    "+1V2_VCCINT":     {"100nF": [f"C{i}" for i in range(19, 28)],  "4.7uF": [f"C{i}" for i in range(40, 47)]},
    "+2V5_VCCA":       {"100nF": ["C28", "C29"],                     "4.7uF": ["C51", "C52"]},
    "+1V2_VCCD_PLL":   {"100nF": ["C38", "C39"],                     "4.7uF": ["C53", "C54"]},
}

DEFAULT_100NF_OFFSET_MM = 0.5   # inside: насколько НЕ доходим до площадки
DEFAULT_47UF_OFFSET_MM = 1.5    # outside: отступ наружу от границы зоны

lines = []
lines.append("rules:")
for net, groups in GROUPS.items():
    pins = pins_sorted_by_angle(net)
    assert pins, f"нет пинов IC1 на цепи {net}!"

    lines.append(f"  - net: \"{net}\"")
    lines.append("    assignments:")

    for value_label, placement, offset_mm in [("100nF", "inside", DEFAULT_100NF_OFFSET_MM),
                                                 ("4.7uF", "outside", DEFAULT_47UF_OFFSET_MM)]:
        caps = groups[value_label]
        for i, ref in enumerate(caps):
            pad = pins[i % len(pins)]
            lines.append(f"      - ref: \"{ref}\"")
            lines.append(f"        pad: \"{pad}\"")
            lines.append(f"        placement: \"{placement}\"")
            lines.append(f"        offset_mm: {offset_mm}")
            lines.append("        via: true")
    lines.append("")

yaml_text = "\n".join(lines)
print(yaml_text)

with open("/home/claude/generated_rules.yaml", "w", encoding="utf-8") as f:
    f.write(yaml_text)

# --- Отчёт: сколько раз каждый пин используется (round-robin по кругу) ---
print("\n--- Использование выводов (round-robin) ---")
for net, groups in GROUPS.items():
    pins = pins_sorted_by_angle(net)
    for value_label in ("100nF", "4.7uF"):
        caps = groups[value_label]
        usage = {}
        for i, ref in enumerate(caps):
            pad = pins[i % len(pins)]
            usage.setdefault(pad, []).append(ref)
        multi = {p: refs for p, refs in usage.items() if len(refs) > 1}
        print(f"{net} / {value_label}: {len(caps)} конд. на {len(pins)} пинов"
              + (f" — на нескольких пинах >1 конденсатора: {multi}" if multi else " — 1:1 или недогруз"))
