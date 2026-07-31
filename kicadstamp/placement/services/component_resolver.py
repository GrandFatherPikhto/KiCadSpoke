# kicadstamp/placement/services/component_resolver.py

import logging
from typing import Dict, List, Optional, Set

from kipy.board_types import FootprintInstance

from ...config import Config
from ...kicad.adapter import KiCadBoardAdapter
from ...exceptions import ValidationError, format_fatal_error
from .component_pool import ComponentPool
from .clone_role_resolver import resolve_footprint_by_role
from ...i18n import _

logger = logging.getLogger(__name__)


def resolve_footprint_by_ref(adapter: KiCadBoardAdapter, anchor_ref: str, label: str,
                             not_found_hint: Optional[str] = None) -> FootprintInstance:
    """Look up a footprint by exact ref, or raise a fatal ValidationError.

    Was written three times near-identically (Rule via ComponentResolver
    below, ClonePlacement, ThermalViaArrayConfig — see
    handoff_2026_07_31_consolidated.md §8 Phase 2) — this is the single
    shared "anchor_ref -> footprint" lookup; the ref-vs-role DECISION and the
    role branch itself stay with each caller, since ClonePlacement's role
    branch needs {placeholder} substitution in anchor_sheet
    (clone_role_resolver.resolve_anchor_by_role) that Rule/
    ThermalViaArrayConfig don't have and don't need — not shared logic, not
    worth forcing through one signature.

    not_found_hint — caller-specific actionable hint line; a generic one is
    used if omitted.
    """
    fp = adapter.get_footprint(anchor_ref)
    if fp is None:
        hint = not_found_hint or _("no such ref on the board (typo? component not yet in PCB?)")
        raise ValidationError(format_fatal_error(
            _("{label}: anchor {anchor!r} not found on board").format(label=label, anchor=anchor_ref),
            [hint]
        ))
    return fp


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

        Returns the footprint instance. Raises a fatal :class:`ValidationError`
        if *anchor_ref* refers to a footprint that doesn't exist on the board.
        """
        if anchor_ref is not None:
            return resolve_footprint_by_ref(self.adapter, anchor_ref, label)
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
