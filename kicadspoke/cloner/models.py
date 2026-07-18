# kicadspoke/cloner/models.py
"""
Модели клонера каналов. Всё файловое: источник истины — .kicad_pcb /
.kicad_sch / .net, никакого IPC (см. issue #24966 и почему запись через
API — рулетка). Координаты — мм, как везде в проекте.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class NetlistComponent:
    """Компонент из нетлиста с иерархическим адресом."""
    ref: str
    value: str
    footprint: str
    sheet_names: str      # "/Channel_0/DAC Sheet/"
    sheet_tstamps: str    # "/uuid-канала/uuid-подлиста/"
    uuid: str             # внутрисхемный uuid символа (хвост tstamps компонента)

    @property
    def channel(self) -> Optional[str]:
        parts = self.sheet_names.strip("/").split("/")
        return parts[0] if parts and parts[0].startswith("Channel") else None

    @property
    def inner_path(self) -> str:
        """Путь внутри канала — общий у близнецов всех каналов."""
        parts = self.sheet_names.strip("/").split("/")
        return "/" + "/".join(parts[1:]) if len(parts) > 1 else "/"

    @property
    def inner_key(self) -> str:
        """Ключ близнецов: внутриканальный путь + uuid символа в шаблоне листа."""
        return f"{self.inner_path}#{self.uuid}"


@dataclass
class ChannelInfo:
    """Экземпляр канала: имя, uuid листа в корневой схеме, файл шаблона."""
    name: str             # "Channel_0"
    sheet_uuid: str       # первый сегмент sheet_tstamps
    components: Dict[str, NetlistComponent] = field(default_factory=dict)  # inner_key -> comp
    local_nets: List[str] = field(default_factory=list)


@dataclass
class TwinMap:
    """
    Соответствие компонентов и цепей между каналами.
    components[inner_key][channel_name] -> ref
    """
    channels: Dict[str, ChannelInfo]
    components: Dict[str, Dict[str, str]]

    def twin_ref(self, ref: str, src_ch: str, dst_ch: str) -> Optional[str]:
        for _, by_ch in self.components.items():
            if by_ch.get(src_ch) == ref:
                return by_ch.get(dst_ch)
        return None

    def twin_net(self, net: str, src_ch: str, dst_ch: str) -> str:
        """Локальная цепь -> цепь близнеца; глобальная возвращается как есть."""
        prefix = f"/{src_ch}/"
        if net.startswith(prefix):
            return f"/{dst_ch}/" + net[len(prefix):]
        return net


@dataclass
class PcbFootprint:
    uuid: str
    ref: str
    lib_id: str
    path: str             # "/ch_uuid/sub_uuid/comp_uuid"
    x_mm: float
    y_mm: float
    rotation_deg: float
    layer: str

    @property
    def channel_uuid(self) -> Optional[str]:
        parts = self.path.strip("/").split("/")
        return parts[0] if parts and parts[0] else None


@dataclass
class PcbSegment:
    uuid: str
    start_x_mm: float
    start_y_mm: float
    end_x_mm: float
    end_y_mm: float
    width_mm: float
    layer: str
    net_id: int
    net_name: str


@dataclass
class PcbVia:
    uuid: str
    x_mm: float
    y_mm: float
    size_mm: float
    drill_mm: float
    layers: List[str]
    net_id: int
    net_name: str


@dataclass
class ChannelPcbSnapshot:
    """Снимок канала на плате: то, что будет клонироваться."""
    channel: str
    channel_uuid: str
    footprints: List[PcbFootprint] = field(default_factory=list)
    segments: List[PcbSegment] = field(default_factory=list)
    vias: List[PcbVia] = field(default_factory=list)
    # Глобальные (не-канальные) сегменты/виа внутри bbox канала — кандидаты
    # на ручное решение (GND-прошивка и т.п.), в клон v1 не входят:
    foreign_segments: List[PcbSegment] = field(default_factory=list)
    foreign_vias: List[PcbVia] = field(default_factory=list)

    def bbox_mm(self):
        xs, ys = [], []
        for f in self.footprints:
            xs.append(f.x_mm); ys.append(f.y_mm)
        for s in self.segments:
            xs += [s.start_x_mm, s.end_x_mm]; ys += [s.start_y_mm, s.end_y_mm]
        for v in self.vias:
            xs.append(v.x_mm); ys.append(v.y_mm)
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))
