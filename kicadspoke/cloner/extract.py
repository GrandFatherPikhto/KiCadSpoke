# kicadspoke/cloner/extract.py
"""
extract-channel: снимок канала в YAML — компоненты с позициями, дорожки,
виа, карта близнецов и сводка. Это «глаза» клонера: до всякой записи
видно, что именно будет клонироваться и что останется за бортом
(foreign-медь глобальных цепей в границах канала).
"""

import logging
from typing import Dict, Any

import yaml

from .netlist import parse_netlist, build_twin_map
from .pcb import PcbDocument
from .models import TwinMap, ChannelPcbSnapshot

logger = logging.getLogger(__name__)


def snapshot_to_dict(snap: ChannelPcbSnapshot, twin: TwinMap) -> Dict[str, Any]:
    ch = snap.channel
    others = [c for c in sorted(twin.channels) if c != ch]

    def twins_of(ref):
        return {o: twin.twin_ref(ref, ch, o) for o in others}

    d: Dict[str, Any] = {
        'channel': ch,
        'channel_sheet_uuid': snap.channel_uuid,
        'summary': {
            'footprints': len(snap.footprints),
            'segments': len(snap.segments),
            'vias': len(snap.vias),
            'foreign_segments_in_bbox': len(snap.foreign_segments),
            'foreign_vias_in_bbox': len(snap.foreign_vias),
        },
        'footprints': [],
        'segments': [],
        'vias': [],
    }
    bb = snap.bbox_mm()
    if bb:
        d['bbox_mm'] = {'x0': round(bb[0], 3), 'y0': round(bb[1], 3),
                        'x1': round(bb[2], 3), 'y1': round(bb[3], 3)}

    for f in sorted(snap.footprints, key=lambda x: x.ref):
        d['footprints'].append({
            'ref': f.ref,
            'lib_id': f.lib_id,
            'x_mm': round(f.x_mm, 4), 'y_mm': round(f.y_mm, 4),
            'rotation_deg': f.rotation_deg,
            'layer': f.layer,
            'uuid': f.uuid,
            'twins': twins_of(f.ref),
        })

    for s in snap.segments:
        d['segments'].append({
            'start': [round(s.start_x_mm, 4), round(s.start_y_mm, 4)],
            'end': [round(s.end_x_mm, 4), round(s.end_y_mm, 4)],
            'width_mm': s.width_mm, 'layer': s.layer,
            'net': s.net_name, 'uuid': s.uuid,
        })

    for v in snap.vias:
        d['vias'].append({
            'at': [round(v.x_mm, 4), round(v.y_mm, 4)],
            'size_mm': v.size_mm, 'drill_mm': v.drill_mm,
            'layers': v.layers, 'net': v.net_name, 'uuid': v.uuid,
        })

    if snap.foreign_segments or snap.foreign_vias:
        d['foreign_in_bbox'] = {
            'note': 'медь ГЛОБАЛЬНЫХ цепей в границах канала: в клон не входит, '
                    'подключение каналов к общим рельсам делается осознанно',
            'segment_nets': sorted({s.net_name for s in snap.foreign_segments}),
            'via_nets': sorted({v.net_name for v in snap.foreign_vias}),
        }
    return d


def extract_channel(net_path: str, pcb_path: str, channel: str,
                    output_yaml: str) -> Dict[str, Any]:
    comps, local_by_ch, _ = parse_netlist(net_path)
    twin = build_twin_map(comps, local_by_ch)
    if channel not in twin.channels:
        raise ValueError(f"канал {channel!r} не найден; есть: {sorted(twin.channels)}")

    doc = PcbDocument(pcb_path)
    snap = doc.snapshot_channel(channel, twin.channels[channel].sheet_uuid)
    d = snapshot_to_dict(snap, twin)

    with open(output_yaml, 'w', encoding='utf-8') as f:
        yaml.safe_dump(d, f, allow_unicode=True, sort_keys=False, width=100)
    logger.info(f"снимок {channel} записан: {output_yaml}")
    return d
