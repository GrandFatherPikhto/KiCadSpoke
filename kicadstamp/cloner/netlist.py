# kicadstamp/cloner/netlist.py
"""
Parsing .net (KiCad 10): components with hierarchical paths, channels,
local/global nets, and the twin map between channels.

Twin key: (path inside the channel, uuid of the symbol in the sheet template).
Since all Channel_N are instances of ONE channel_tpl.kicad_sch, twins have
identical keys byte‑for‑byte. Refdes is NOT used for mapping on principle:
numbering between channels is non‑monotonic (observed on mishin‑coil:
FB602->FB1102->FB1602 while C602->C1602->C1102).
"""

import logging
import re
from collections import defaultdict


from .sexp import load_file, children, child, atom
from .models import NetlistComponent, ChannelInfo, TwinMap
from ..i18n import _

logger = logging.getLogger(__name__)

CHANNEL_RE = re.compile(r'^/(?P<ch>Channel_\d+)(?:/|$)')


def parse_netlist(net_path: str) -> tuple[list[NetlistComponent], dict[str, list[str]], list[str]]:
    """
    -> (components, local nets by channel, global nets).
    unconnected‑* are filtered out.
    """
    root = load_file(net_path)
    comps_node = child(root, 'components') or []
    comps: list[NetlistComponent] = []
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
    local_by_ch: dict[str, list[str]] = defaultdict(list)
    global_nets: list[str] = []
    for n in children(nets_node, 'net'):
        name = atom(n, 'name', '')
        if not name or name.startswith('unconnected'):
            continue
        m = CHANNEL_RE.match(name)
        (local_by_ch[m.group('ch')] if m else global_nets).append(name)

    logger.info(_("netlist: {comps} components, channels with local nets: {channels}, global nets: {global_nets}")
                .format(comps=len(comps), channels=len(local_by_ch), global_nets=len(global_nets)))
    return comps, dict(local_by_ch), global_nets


def build_twin_map(comps: list[NetlistComponent],
                   local_by_ch: dict[str, list[str]]) -> TwinMap:
    """Channels + twin map. Incomplete groups (not present in all channels) are warned."""
    channels: dict[str, ChannelInfo] = {}
    twin: dict[str, dict[str, str]] = defaultdict(dict)

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
            logger.warning(_("incomplete twin group [{key}]: present only in {channels}")
                           .format(key=k, channels=sorted(v)))
        logger.warning(_("total incomplete groups: {count} — these components cannot be cloned by mapping")
                       .format(count=len(incomplete)))
    logger.info(_("channels: {count} ({names}); complete twin groups: {complete} of {total}")
                .format(count=n_ch, names=', '.join(sorted(channels)),
                        complete=len(twin) - len(incomplete), total=len(twin)))
    return TwinMap(channels=channels, components=dict(twin))