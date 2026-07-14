# decap_placer/placement/services/interfaces.py
from abc import ABC, abstractmethod
from typing import List, Set, Tuple, Optional
from kipy.board_types import FootprintInstance
from ...geometry.keepout import Rect

class IKeepoutBuilder(ABC):
    @abstractmethod
    def build_keepout(self, target_fp: FootprintInstance,
                      cap_refs: Set[str],
                      exclude: Optional[Set[Tuple[str, str]]] = None) -> List[Rect]:
        pass