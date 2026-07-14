# decap_placer/placement/services/keepout_builder.py
from typing import List, Tuple, Optional, Set
from kipy.board_types import FootprintInstance, Pad
from .interfaces import IKeepoutBuilder
from ...geometry.keepout import Rect, build_keepout
from ...kicad.adapter import KiCadBoardAdapter
from ...config import Config

class KeepoutBuilder(IKeepoutBuilder):
    
    def __init__(self, adapter: KiCadBoardAdapter, config: Config):
        self.adapter = adapter
        self.cfg = config

    def build_keepout(self, target_fp: FootprintInstance,
                      cap_refs: Set[str],
                      exclude: Optional[Set[Tuple[str, str]]] = None) -> List[Rect]:
        exclude = exclude or set()
        def collect(ref: str, fp: FootprintInstance) -> List[Pad]:
            return [p for p in self.adapter.get_footprint_pads(fp) if (ref, p.number) not in exclude]

        pad_items = collect(self.cfg.target_ref, target_fp)
        for ref in sorted(cap_refs):
            fp = self.adapter.get_footprint(ref)
            if fp is None:
                continue
            pad_items.extend(collect(ref, fp))

        bboxes = self.adapter.get_bounding_boxes(pad_items)
        return build_keepout(bboxes, self.cfg.via_keepout_clearance_mm)