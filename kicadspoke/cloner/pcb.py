# kicadspoke/cloner/pcb.py
"""
Разбор .kicad_pcb: футпринты (с иерархическим path), сегменты, виа,
таблица цепей — и выборка «всё канала N».

Ключ выборки футпринтов — ПЕРВЫЙ сегмент (path "/ch_uuid/.../comp_uuid"),
сверенный с uuid листа канала из нетлиста. Никакого матчинга по именам.

Сегменты/виа каналу принадлежат ПО ЦЕПИ: цепь с префиксом /Channel_N/ —
канальная. Элементы ГЛОБАЛЬНЫХ цепей (GND, рельсы) внутри bbox канала
собираются отдельно (foreign_*): в клон v1 они не входят, но о них надо
знать — это GND-прошивка и подводы питания, которые после клонирования
делаются осознанно.
"""

import logging
from typing import Dict, List, Tuple

from .sexp import load_file, children, child, atom, sval, is_node
from .models import PcbFootprint, PcbSegment, PcbVia, ChannelPcbSnapshot

logger = logging.getLogger(__name__)


def _num(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class PcbDocument:
    def __init__(self, pcb_path: str):
        logger.info(f"читаю плату: {pcb_path}")
        self.root = load_file(pcb_path)
        self.net_names: Dict[int, str] = {}
        for n in children(self.root, 'net'):
            # (net 42 "имя") — только верхнеуровневые объявления
            if len(n) >= 3:
                self.net_names[int(n[1])] = sval(n[2])
        self._net_ids_by_name = {v: k for k, v in self.net_names.items()}
        self.footprints = self._parse_footprints()
        self.segments = self._parse_segments()
        self.vias = self._parse_vias()
        # KiCad 10: числовой таблицы цепей в pcb больше нет, цепи по именам
        used_nets = {s.net_name for s in self.segments} | {v.net_name for v in self.vias}
        used_nets.discard('')
        logger.info(f"плата: {len(self.footprints)} футпринтов, "
                    f"{len(self.segments)} сегментов, {len(self.vias)} виа; "
                    f"цепей в меди: {len(used_nets)}")

    # --- низкоуровневые парсеры ---

    def _parse_footprints(self) -> List[PcbFootprint]:
        out = []
        for fp in children(self.root, 'footprint'):
            at = child(fp, 'at') or [None, 0, 0]
            ref = ''
            for prop in children(fp, 'property'):
                if len(prop) >= 3 and sval(prop[1]) == 'Reference':
                    ref = sval(prop[2])
                    break
            out.append(PcbFootprint(
                uuid=atom(fp, 'uuid', ''),
                ref=ref,
                lib_id=sval(fp[1]) if len(fp) > 1 else '',
                path=atom(fp, 'path', ''),
                x_mm=_num(at[1]),
                y_mm=_num(at[2]) if len(at) > 2 else 0.0,
                rotation_deg=_num(at[3]) if len(at) > 3 else 0.0,
                layer=atom(fp, 'layer', ''),
            ))
        return out

    def _net_ref(self, node) -> Tuple[int, str]:
        """
        (net X)元: в KiCad 10 X может быть числовым id ИЛИ строкой-именем
        (наблюдается на реальных платах 10.0.4). Возвращаем (id, имя);
        отсутствующая половина восстанавливается по таблице цепей.
        """
        raw = atom(node, 'net', None)
        if raw is None:
            return 0, ''
        if isinstance(raw, int):
            return raw, self.net_names.get(raw, '')
        name = str(raw)
        return self._net_ids_by_name.get(name, 0), name

    def _parse_segments(self) -> List[PcbSegment]:
        out = []
        for s in children(self.root, 'segment'):
            st = child(s, 'start') or [None, 0, 0]
            en = child(s, 'end') or [None, 0, 0]
            net_id, net_name = self._net_ref(s)
            out.append(PcbSegment(
                uuid=atom(s, 'uuid', ''),
                start_x_mm=_num(st[1]), start_y_mm=_num(st[2]),
                end_x_mm=_num(en[1]), end_y_mm=_num(en[2]),
                width_mm=_num(atom(s, 'width', 0)),
                layer=atom(s, 'layer', ''),
                net_id=net_id,
                net_name=net_name,
            ))
        return out

    def _parse_vias(self) -> List[PcbVia]:
        out = []
        for v in children(self.root, 'via'):
            at = child(v, 'at') or [None, 0, 0]
            layers_node = child(v, 'layers') or []
            net_id, net_name = self._net_ref(v)
            out.append(PcbVia(
                uuid=atom(v, 'uuid', ''),
                x_mm=_num(at[1]), y_mm=_num(at[2]),
                size_mm=_num(atom(v, 'size', 0)),
                drill_mm=_num(atom(v, 'drill', 0)),
                layers=[sval(x) for x in layers_node[1:]] if layers_node else [],
                net_id=net_id,
                net_name=net_name,
            ))
        return out

    # --- выборка канала ---

    def snapshot_channel(self, channel_name: str, channel_uuid: str,
                         bbox_margin_mm: float = 1.0) -> ChannelPcbSnapshot:
        snap = ChannelPcbSnapshot(channel=channel_name, channel_uuid=channel_uuid)
        for f in self.footprints:
            if f.channel_uuid == channel_uuid:
                snap.footprints.append(f)

        net_prefix = f"/{channel_name}/"
        for s in self.segments:
            if s.net_name.startswith(net_prefix):
                snap.segments.append(s)
        for v in self.vias:
            if v.net_name.startswith(net_prefix):
                snap.vias.append(v)

        # Чужаки в границах канала (GND-прошивка, подводы питания)
        bbox = snap.bbox_mm()
        if bbox:
            x0, y0, x1, y1 = bbox
            x0 -= bbox_margin_mm; y0 -= bbox_margin_mm
            x1 += bbox_margin_mm; y1 += bbox_margin_mm
            inside = lambda x, y: x0 <= x <= x1 and y0 <= y <= y1
            for s in self.segments:
                if not s.net_name.startswith(net_prefix) and \
                        (inside(s.start_x_mm, s.start_y_mm) or inside(s.end_x_mm, s.end_y_mm)):
                    snap.foreign_segments.append(s)
            for v in self.vias:
                if not v.net_name.startswith(net_prefix) and inside(v.x_mm, v.y_mm):
                    snap.foreign_vias.append(v)

        logger.info(f"{channel_name}: {len(snap.footprints)} футпринтов, "
                    f"{len(snap.segments)} сегментов, {len(snap.vias)} виа; "
                    f"чужих в bbox: {len(snap.foreign_segments)} сегм., "
                    f"{len(snap.foreign_vias)} виа")
        return snap
