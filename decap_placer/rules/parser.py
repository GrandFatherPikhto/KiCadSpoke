# decap_placer/rules/parser.py

import sexpdata
from sexpdata import Symbol
import math
from typing import Dict, List, Tuple, Optional

def tag(item):
    return str(item[0]) if isinstance(item, list) and item and isinstance(item[0], Symbol) else None

def find_all(sexp, t):
    return [i for i in sexp if isinstance(i, list) and tag(i) == t]

def find_first(sexp, t):
    r = find_all(sexp, t)
    return r[0] if r else None

def get_str(sexp, t):
    for item in sexp:
        if isinstance(item, list) and tag(item) == t:
            for v in item[1:]:
                if isinstance(v, str):
                    return v
                if isinstance(v, Symbol):
                    return str(v)
    return None

def parse_net_file(net_path: str) -> Dict[str, List[Tuple[str, str]]]:
    """Возвращает словарь: имя_цепи -> список (ref, pin)."""
    with open(net_path, encoding="utf-8") as f:
        net_data = sexpdata.load(f)
    nets_root = find_first(net_data, "nets")
    net_nodes = {}
    for net in find_all(nets_root, "net"):
        name = get_str(net, "name")
        nodes = []
        for node in find_all(net, "node"):
            ref = get_str(node, "ref")
            pin = get_str(node, "pin")
            if ref and pin:
                nodes.append((ref, pin))
        net_nodes[name] = nodes
    return net_nodes

def parse_pcb_file(pcb_path: str) -> Tuple[Dict[str, Tuple[float, float, float]], Dict[str, Dict[str, Tuple[float, float]]]]:
    """
    Возвращает:
        - fp_info: {ref: (x, y, angle)}
        - pads_info: {ref: {pad_number: (x, y)}}
    """
    with open(pcb_path, encoding="utf-8") as f:
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
    fp_info = {}
    pads_info = {}
    for fp in footprints:
        ref = get_reference(fp)
        if not ref:
            continue
        at = find_first(fp, "at")
        if not at:
            continue
        x, y = float(at[1]), float(at[2])
        angle = float(at[3]) if len(at) > 3 else 0.0
        fp_info[ref] = (x, y, angle)

        pads = {}
        for pad in find_all(fp, "pad"):
            pad_num = str(pad[1])
            pat = find_first(pad, "at")
            if pat:
                lx, ly = float(pat[1]), float(pat[2])
                rx, ry = rotate(lx, ly, angle)
                pads[pad_num] = (x + rx, y + ry)
        pads_info[ref] = pads

    return fp_info, pads_info