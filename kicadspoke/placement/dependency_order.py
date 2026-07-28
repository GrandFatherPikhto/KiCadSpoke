# kicadspoke/placement/dependency_order.py
"""
dependency_order.py — determines the order in which rules/clone_placements
must be planned+executed within one apply run, so that an item anchored on a
component another item in the SAME run is about to move always sees that
component's real, post-move position — not a stale snapshot from before the
run started.

Found 2026-07-27: p5v_led_spoke was anchored on C9, a role slot inside
p5v_pi_filter's own template — it landed wherever C9 last happened to sit
manually, not where p5v_pi_filter was about to move it to, because the whole
run used to plan from a single board snapshot taken before any moves happened.

Level-by-level (Kahn's algorithm — this is exactly the "build the chain first,
then move and fix" picture): level 0 = items with no anchor at all (absolute
coordinates), or anchored on something nobody in this run moves ("taken
as-is"); level 1 = items anchored on something level 0 produces; level 2 =
anchored on level 0/1 output; etc. cmd_apply plans+executes+commits one whole
level before moving to the next, so by the time a later level's anchor is
resolved, the board already reflects the earlier levels' real moves.

Known limitation: template-role resolution (which refs get PRODUCED) can, as a
last resort, use physical proximity to the anchor to break ties between
otherwise-identical candidates (see
clone_role_resolver._narrow_ambiguous_candidates). Since this dependency pass
runs against the board BEFORE any moves happen, such a tie could in principle
resolve differently here than it will once execution actually reaches that
item post-move. Only matters for configs that are already relying on a
proximity tie-break to disambiguate; not a regression versus the old
single-snapshot behaviour.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Set, Union

from ..config import Config, Rule, ClonePlacement
from ..kicad.adapter import KiCadBoardAdapter
from ..exceptions import ValidationError, format_fatal_error
from .services.manual_position_calculator import ManualPositionCalculator, resolve_rule_anchor_ref
from .services.clone_position_calculator import ClonePositionCalculator, resolve_clone_anchor_ref
from ..i18n import _

logger = logging.getLogger(__name__)


@dataclass
class Item:
    kind: str  # 'rule' | 'clone'
    obj: Union[Rule, ClonePlacement]
    label: str
    anchor_ref: Optional[str]
    produces: Set[str]


def _build_items(adapter: KiCadBoardAdapter, cfg: Config) -> List[Item]:
    """Read-only: resolves every enabled rule/clone_placement's anchor ref and
    produced refs against the board as it is RIGHT NOW. No board mutation —
    same calls compute_raw_positions already makes for planning."""
    items: List[Item] = []
    position_calc = ManualPositionCalculator(adapter, cfg)
    clone_calc = ClonePositionCalculator(adapter, cfg)

    for rule in cfg.rules:
        anchor_ref = resolve_rule_anchor_ref(adapter, cfg, rule)
        placed, _vias, _tracks = position_calc.compute_raw_positions([rule])
        items.append(Item(
            kind='rule', obj=rule, label=_("rule (net {net!r})").format(net=rule.net),
            anchor_ref=anchor_ref, produces={p.ref for p in placed},
        ))

    for clone in cfg.clone_placements:
        if not clone.enabled:
            continue
        anchor_ref = resolve_clone_anchor_ref(adapter, cfg, clone)
        placed, _vias, _tracks = clone_calc.compute_raw_positions([clone])
        items.append(Item(
            kind='clone', obj=clone, label=_("clone_placement {name!r}").format(name=clone.name),
            anchor_ref=anchor_ref, produces={p.ref for p in placed},
        ))

    return items


def resolve_execution_order(adapter: KiCadBoardAdapter, cfg: Config) -> List[Item]:
    """
    Read-only: resolves cfg.rules + cfg.clone_placements (already filtered by
    drop_disabled_rules/apply_only_filter/apply_cluster_filter) into
    level-ordered execution order. Raises ValidationError on a dependency
    cycle — a config where two or more items anchor on each other's output has
    no valid order and must be fixed in the YAML.
    """
    items = _build_items(adapter, cfg)

    producer_of = {}  # ref -> Item that produces it
    for item in items:
        for ref in item.produces:
            producer_of[ref] = item

    remaining = list(items)
    ordered: List[Item] = []
    placed_ids = set()

    while remaining:
        level = [
            it for it in remaining
            if it.anchor_ref is None
            or producer_of.get(it.anchor_ref) is None
            # An item anchored on its OWN pad (e.g. a template whose origin
            # component is also one of its own role slots — seen live with
            # p5v_led_spoke: anchored on R1.pad2, and R1/R_LED is also a role
            # in its own template) is not a real cross-item dependency — there
            # is no other item to sequence against, so treat it as satisfied.
            or producer_of[it.anchor_ref] is it
            or id(producer_of[it.anchor_ref]) in placed_ids
        ]
        if not level:
            raise ValidationError(format_fatal_error(
                _("dependency cycle among rules/clone_placements"),
                [_("{count} item(s) form a cycle through their anchors: {items}")
                 .format(count=len(remaining), items=", ".join(it.label for it in remaining)),
                 _("break the cycle: at least one of these must anchor on something "
                   "outside this set (a fixed, pre-existing component, or an absolute "
                   "coordinate)")]
            ))
        ordered.extend(level)
        level_ids = {id(it) for it in level}
        placed_ids.update(level_ids)
        remaining = [it for it in remaining if id(it) not in level_ids]

    return ordered
