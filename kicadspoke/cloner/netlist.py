# kicadspoke/cloner/netlist.py
"""
Разбор .net (KiCad 10): компоненты с иерархическими путями, каналы,
локальные/глобальные цепи и карта близнецов между каналами.

Ключ близнецов: (путь внутри канала, uuid символа в файле-шаблоне листа).
Поскольку все Channel_N — экземпляры ОДНОГО channel_tpl.kicad_sch, у
близнецов эти ключи совпадают побайтово. Refdes для маппинга не
используется принципиально: нумерация между каналами не монотонна
(наблюдалось на mishin-coil: FB602->FB1102->FB1602 при C602->C1602->C1102).
"""

import logging
import re
from collections import defaultdict
from typing import Dict, List, Tuple

from .sexp import load_file, children, child, atom
from .models import NetlistComponent, ChannelInfo, TwinMap

logger = logging.getLogger(__name__)

CHANNEL_RE = re.compile(r'^/(?P<ch>Channel_\d+)(?:/|$)')


def parse_netlist(net_path: str) -> Tuple[List[NetlistComponent], Dict[str, List[str]], List[str]]:
    """
    -> (компоненты, локальные цепи по каналам, глобальные цепи).
    unconnected-* отфильтрованы.
    """
    root = load_file(net_path)
    comps_node = child(root, 'components') or []
    comps: List[NetlistComponent] = []
    for c in children(comps_node, 'comp'):
        sp = child(c, 'sheetpath')
        tstamps = atom(c, 'tstamps', '') or ''
        comps.append(NetlistComponent(
            ref=atom(c, 'ref', ''),
            value=atom(c, 'value', ''),
            footprint=atom(c, 'footprint', ''),
            sheet_names=(atom(sp, 'names', '/') if sp else '/'),
            sheet_tstamps=(atom(sp, 'tstamps', '/') if sp else '/'),
            uuid=tstamps.strip('/').split('/')[-1] if tstamps else '',
        ))

    nets_node = child(root, 'nets') or []
    local_by_ch: Dict[str, List[str]] = defaultdict(list)
    global_nets: List[str] = []
    for n in children(nets_node, 'net'):
        name = atom(n, 'name', '')
        if not name or name.startswith('unconnected'):
            continue
        m = CHANNEL_RE.match(name)
        (local_by_ch[m.group('ch')] if m else global_nets).append(name)

    logger.info(f"нетлист: {len(comps)} компонентов, каналов с локальными цепями: "
                f"{len(local_by_ch)}, глобальных цепей: {len(global_nets)}")
    return comps, dict(local_by_ch), global_nets


def build_twin_map(comps: List[NetlistComponent],
                   local_by_ch: Dict[str, List[str]]) -> TwinMap:
    """Каналы + карта близнецов. Неполные группы (не во всех каналах) — warning."""
    channels: Dict[str, ChannelInfo] = {}
    twin: Dict[str, Dict[str, str]] = defaultdict(dict)

    for c in comps:
        ch = c.channel
        if ch is None:
            continue
        if ch not in channels:
            sheet_uuid = c.sheet_tstamps.strip('/').split('/')[0]
            channels[ch] = ChannelInfo(name=ch, sheet_uuid=sheet_uuid,
                                       local_nets=sorted(local_by_ch.get(ch, [])))
        channels[ch].components[c.inner_key] = c
        twin[c.inner_key][ch] = c.ref

    n_ch = len(channels)
    incomplete = {k: v for k, v in twin.items() if len(v) != n_ch}
    if incomplete:
        for k, v in list(incomplete.items())[:10]:
            logger.warning(f"неполная группа близнецов [{k}]: есть только в {sorted(v)}")
        logger.warning(f"всего неполных групп: {len(incomplete)} — "
                       f"эти компоненты клонироваться по маппингу не смогут")
    logger.info(f"каналов: {n_ch} ({', '.join(sorted(channels))}); "
                f"полных групп близнецов: {len(twin) - len(incomplete)} из {len(twin)}")
    return TwinMap(channels=channels, components=dict(twin))
