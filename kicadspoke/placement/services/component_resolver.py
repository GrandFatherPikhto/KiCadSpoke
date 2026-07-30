# kicadspoke/placement/services/component_resolver.py

import logging
from typing import Dict, List, Optional, Set

from kipy.board_types import FootprintInstance

from ...config import Config
from ...kicad.adapter import KiCadBoardAdapter
from ...exceptions import ComponentNotFoundError
from .component_pool import ComponentPool
from .clone_role_resolver import resolve_footprint_by_role
from ...i18n import _

logger = logging.getLogger(__name__)


class ComponentResolver:
    """Common logic shared by ``ManualPositionCalculator`` (and, in a
    structurally similar way, ``ClonePositionCalculator``):

    * Resolve an anchor footprint by ref (``anchor_ref``) **or** by role
      (+ sheet + cluster).
    * Build :class:`ComponentPool`` instances per cluster for role-based
      footprint allocation.

    Removes the duplicated "ref vs role" branching that both calculators
    had inline.
    """

    def __init__(self, adapter: KiCadBoardAdapter, config: Config,
                 sheet_names: Dict[str, str]):
        self.adapter = adapter
        self.cfg = config
        self.sheet_names = sheet_names

    def resolve_anchor_fp(self,
                          anchor_ref: Optional[str],
                          anchor_role: Optional[str],
                          anchor_sheet: Optional[str],
                          anchor_cluster: Optional[str],
                          label: str = "") -> FootprintInstance:
        """Resolve a footprint by ref **or** by role/sheet/cluster.

        Returns the footprint instance. Raises
        :class:`ComponentNotFoundError` if *anchor_ref* refers to a
        footprint that doesn't exist on the board.
        """
        if anchor_ref is not None:
            fp = self.adapter.get_footprint(anchor_ref)
            if fp is None:
                raise ComponentNotFoundError(
                    _("{label}: anchor {anchor!r} not found on board")
                    .format(label=label, anchor=anchor_ref)
                )
            return fp
        return resolve_footprint_by_role(
            self.adapter, anchor_role, anchor_sheet, anchor_cluster,
            self.sheet_names, label=label,
        )

    @staticmethod
    def build_pools(adapter: KiCadBoardAdapter, net: str,
                    roles_needed: Set[str],
                    clusters_needed: Set[Optional[str]]
                    ) -> Dict[Optional[str], ComponentPool]:
        """Build a :class:`ComponentPool` for each cluster in
        *clusters_needed*.

        Returns ``{cluster_name: ComponentPool}`` — one pool per cluster,
        each covering all *roles_needed*.
        """
        return {
            cluster: ComponentPool(adapter, net,
                                   roles=sorted(roles_needed),
                                   cluster=cluster)
            for cluster in clusters_needed
        }
