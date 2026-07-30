# kicadspoke/apply_pipeline.py
"""
apply_pipeline.py — orchestrates the full ``apply`` pipeline.

Extracted from kicadspoke_cli.py's cmd_apply() to separate pipeline orchestration
from CLI argument parsing.  The pipeline is:

    load config
      -> compute known_anchor_ids (for registry protection)
      -> filter (disabled, active, --only, --cluster)
      -> connect KiCad adapter
      -> validate
      -> resolve dependency order
      -> create planner
      -> [dry‑run: plan & print]
      -> [execute: move → refresh → vias → tracks, per level]
"""

import dataclasses
import difflib
import logging
import sys
from typing import Dict, Any, List, Optional, Set

from kipy.errors import ApiError, ApiStatusCode

from .config import load_config, rule_effective_name, thermal_via_array_effective_name
from .kicad.adapter import KiCadBoardAdapter
from .placement.planner import PlacementPlanner
from .placement.dependency_order import resolve_execution_order
from .placement.services.clone_position_calculator import clone_anchor_id
from .placement.services.via_planner import thermal_anchor_id
from .placement.services.manual_position_calculator import rule_anchor_ids
from .placement.services.component_pool import _cluster_prefix_match
from .placement.executor import BatchExecutor
from .exceptions import PlacerError
from .validation import run_all_checks
from .registry import (PlacementRegistry, registry_path_for_config,
                       TrackRegistry, track_registry_path_for_config)
from .i18n import _

logger = logging.getLogger(__name__)


# ── Filtering helpers (pure cfg mutation, no adapter) ─────────────────────────


def _split_comma_values(raw: Optional[List[str]]) -> List[str]:
    """--only/--cluster accept both repeating the flag and comma‑separated
    values within one occurrence (e.g. --only a,b --only c -> [a, b, c])."""
    if not raw:
        return []
    result = []
    for item in raw:
        result.extend(part.strip() for part in item.split(",") if part.strip())
    return result


def _matches_any_cluster(candidate: Optional[str], wanted: List[str]) -> bool:
    if candidate is None:
        return False
    return any(_cluster_prefix_match(candidate, w) for w in wanted)


def drop_disabled_rules(cfg, _logger=None) -> None:
    """enabled: false always wins, dropped before --only/--cluster ever see
    it — enabled means "does not exist on the board right now", not
    "excluded from this particular run" (see Rule docstring in config/models.py).
    Pure cfg mutation, no adapter — kept separate so it's unit‑testable without
    a live KiCad connection."""
    l = _logger or logger
    disabled_rules = [r for r in cfg.rules if not r.enabled]
    cfg.rules = [r for r in cfg.rules if r.enabled]
    for r in disabled_rules:
        l.info(_("Rule {name!r} (net {net!r}): enabled=false, skipped entirely")
               .format(name=rule_effective_name(r), net=r.net))


def drop_inactive_items(cfg, _logger=None) -> None:
    """active: false — the inline, per-item counterpart of --only/--cluster
    (see Rule/ClonePlacement/ThermalViaArrayConfig.active docstrings in
    config/models.py). Unlike enabled: false (drop_disabled_rules above),
    this must run AFTER known_anchor_ids is computed — an inactive item's
    via/tracks must still count as "known" so reconcile() protects them from
    pruning, it's just not (re)planned this run. Composes with --only/--cluster
    as a further AND-narrowing. Pure cfg mutation, no adapter."""
    l = _logger or logger
    active_clones = [c for c in cfg.clone_placements if c.active]
    dropped_clones = [c for c in cfg.clone_placements if not c.active]
    cfg.clone_placements = active_clones
    for c in dropped_clones:
        l.info(_("ClonePlacement {name!r}: active=false, skipped this run "
                  "(existing via/tracks stay protected)").format(name=c.name))

    narrowed_rules = []
    for r in cfg.rules:
        if not r.active:
            l.info(_("Rule {name!r}: active=false, skipped this run "
                      "(existing via/tracks stay protected)").format(name=rule_effective_name(r)))
            continue
        kept_spokes = [s for s in r.spokes if s.active]
        for s in r.spokes:
            if not s.active:
                l.debug(_("Rule {name!r}: spoke on pad {pad} active=false, skipped this run")
                         .format(name=rule_effective_name(r), pad=s.pad))
        if kept_spokes:
            narrowed_rules.append(dataclasses.replace(r, spokes=kept_spokes))
        else:
            l.info(_("Rule {name!r}: no active spokes left, skipped this run "
                      "(existing via/tracks stay protected)").format(name=rule_effective_name(r)))
    cfg.rules = narrowed_rules

    if cfg.thermal_via_array.enabled and not cfg.thermal_via_array.active:
        l.info(_("thermal_via_array {name!r}: active=false, skipped this run "
                  "(existing vias stay protected)")
               .format(name=thermal_via_array_effective_name(cfg.thermal_via_array)))
        cfg.thermal_via_array.enabled = False


def apply_only_filter(cfg, only_names: List[str], _logger=None) -> None:
    """--only: whole-block selection by identity (rule name-or-net, clone_placement
    name, thermal_via_array name). sys.exit on unmatched names. Pure cfg mutation."""
    l = _logger or logger
    if not only_names:
        return
    requested = set(only_names)
    matched_rules = [r for r in cfg.rules if rule_effective_name(r) in requested]
    matched_clones = [c for c in cfg.clone_placements if c.name in requested]
    thermal_matches = (cfg.thermal_via_array.enabled and
                       thermal_via_array_effective_name(cfg.thermal_via_array) in requested)

    found_names = ({rule_effective_name(r) for r in matched_rules}
                   | {c.name for c in matched_clones}
                   | ({thermal_via_array_effective_name(cfg.thermal_via_array)}
                      if thermal_matches else set()))
    missing = requested - found_names
    if missing:
        all_names = sorted(
            {rule_effective_name(r) for r in cfg.rules}
            | {c.name for c in cfg.clone_placements}
            | ({thermal_via_array_effective_name(cfg.thermal_via_array)}
               if cfg.thermal_via_array.enabled else set())
        )
        lines = []
        for name in sorted(missing):
            suggestion = difflib.get_close_matches(name, all_names, n=1)
            hint = (_(" (maybe you meant {suggestion!r}?)").format(suggestion=suggestion[0])
                    if suggestion else "")
            lines.append(_("  {name!r} — not found among rules, clone_placements, "
                           "or thermal_via_array{hint}")
                         .format(name=name, hint=hint))
        sys.exit(_("[error] --only: names not found:\n{lines}\nAvailable: {all}")
                 .format(lines="\n".join(lines), all=all_names))

    cfg.rules = matched_rules
    cfg.clone_placements = matched_clones
    if not thermal_matches:
        cfg.thermal_via_array.enabled = False
    l.info(_("--only {requested}: rules={rules}, clone_placements={clones}, "
              "thermal_via_array={thermal} (everything else is ignored in this run)")
            .format(requested=sorted(requested),
                    rules=[rule_effective_name(r) for r in matched_rules],
                    clones=[c.name for c in matched_clones],
                    thermal=_("yes") if thermal_matches else _("no")))


def apply_cluster_filter(cfg, cluster_paths: List[str], _logger=None) -> None:
    """--cluster — a second, independent selection axis (physical instance /
    Cluster field, not name). Composes with --only via AND only, never OR.
    Pure cfg mutation, no adapter."""
    l = _logger or logger
    if not cluster_paths:
        return
    matched_clones = [c for c in cfg.clone_placements
                      if _matches_any_cluster(c.anchor_cluster, cluster_paths)]
    thermal_matches = (cfg.thermal_via_array.enabled and
                       _matches_any_cluster(cfg.thermal_via_array.anchor_cluster, cluster_paths))

    narrowed_rules = []
    for r in cfg.rules:
        kept_spokes = [s for s in r.spokes if _matches_any_cluster(s.cluster, cluster_paths)]
        if kept_spokes:
            narrowed_rules.append(dataclasses.replace(r, spokes=kept_spokes))
        else:
            l.debug(_("Rule {name!r}: no spokes match --cluster {paths}, rule dropped")
                     .format(name=rule_effective_name(r), paths=cluster_paths))

    if not narrowed_rules and not matched_clones and not thermal_matches:
        sys.exit(_("[error] --cluster {paths}: matched nothing among rules' spokes, "
                   "clone_placements, or thermal_via_array").format(paths=cluster_paths))

    cfg.rules = narrowed_rules
    cfg.clone_placements = matched_clones
    if not thermal_matches:
        cfg.thermal_via_array.enabled = False
    l.info(_("--cluster {paths}: rules={rules} (spokes narrowed), "
              "clone_placements={clones}, thermal_via_array={thermal}")
            .format(paths=cluster_paths,
                    rules=[rule_effective_name(r) for r in narrowed_rules],
                    clones=[c.name for c in matched_clones],
                    thermal=_("yes") if thermal_matches else _("no")))


# ── Compute helper ────────────────────────────────────────────────────────────

def _compute_all_anchor_ids(cfg) -> Set[str]:
    """Build the FULL set of anchor IDs (before --only/--cluster narrow)
    for registry.reconcile()'s known_anchor_ids protection."""
    ids = {clone_anchor_id(c) for c in cfg.clone_placements if c.enabled}
    for r in cfg.rules:
        ids |= rule_anchor_ids(r)
    if cfg.thermal_via_array.enabled:
        ids.add(thermal_anchor_id(cfg.thermal_via_array))
    return ids


# ── Pipeline ──────────────────────────────────────────────────────────────────

class ApplyPipeline:
    """
    Orchestrates the full ``apply`` pipeline.

    Separated from argument parsing so the pipeline is independently testable
    and cmd_apply() stays a thin delegator.
    """

    def __init__(
        self,
        config_path: str,
        *,
        timeout_ms: int = 20000,
        batch_size: int = 10,
        dry_run: bool = False,
        no_selection: bool = False,
        no_collision_check: bool = False,
        collision_margin: float = 0.2,
        only: Optional[List[str]] = None,
        cluster: Optional[List[str]] = None,
        preloaded_cfg=None,
    ):
        self.config_path = config_path
        self.timeout_ms = timeout_ms
        self.batch_size = batch_size
        self.dry_run = dry_run
        self.no_selection = no_selection
        self.no_collision_check = no_collision_check
        self.collision_margin = collision_margin
        self.only = only or []
        self.cluster = cluster or []

        # Internal state
        self.cfg = preloaded_cfg
        self.adapter: Optional[KiCadBoardAdapter] = None
        self.planner: Optional[PlacementPlanner] = None
        self.items = None
        self.all_anchor_ids: Set[str] = set()

    # ── Pipeline steps ──────────────────────────────────────────────────────

    def _load_config(self) -> None:
        if self.cfg is None:
            logger.info(_("Loading config: {config}").format(config=self.config_path))
            self.cfg = load_config(self.config_path)

    def _filter_config(self) -> None:
        drop_disabled_rules(self.cfg)
        self.all_anchor_ids = _compute_all_anchor_ids(self.cfg)
        drop_inactive_items(self.cfg)
        apply_only_filter(self.cfg, _split_comma_values(self.only))
        apply_cluster_filter(self.cfg, _split_comma_values(self.cluster))

    def _connect_adapter(self) -> None:
        logger.info(_("Connecting to KiCad (timeout {timeout} ms)").format(timeout=self.timeout_ms))
        self.adapter = KiCadBoardAdapter(timeout_ms=self.timeout_ms)
        if self.no_selection:
            self.adapter.ignore_selection = True
            logger.info(_("--no-selection: current PCB editor selection will be ignored for this run"))
        self.adapter.refresh_board()

    def _validate(self) -> None:
        run_all_checks(self.adapter, self.cfg)

    def _resolve_order(self) -> None:
        logger.info(_("Resolving item execution order (dependency chain — see dependency_order.py)..."))
        self.items = resolve_execution_order(self.adapter, self.cfg)
        logger.info(_("Execution order: {order}")
                    .format(order=" -> ".join(it.label for it in self.items)))

    def _create_planner(self) -> None:
        self.planner = PlacementPlanner(self.adapter, self.cfg)

    # ── Dry‑run ─────────────────────────────────────────────────────────────

    def _dry_run(self) -> None:
        moves = self.planner.plan_items(self.items)
        vias = self.planner.plan_vias()
        tracks = self.planner.plan_tracks()
        print("\n=== DRY RUN ===")
        print(_("Order: {order}").format(order=" -> ".join(it.label for it in self.items)))
        print(_("Moves:"))
        for m in moves:
            print(_("  {ref}: ({x:.3f}, {y:.3f}) mm, angle={angle:.1f}°")
                  .format(ref=m.ref, x=m.position.x / 1e6, y=m.position.y / 1e6,
                          angle=m.angle.degrees))
        print("\n" + _("Vias:"))
        for v in vias:
            print(_("  via for {owner}: ({x:.3f}, {y:.3f}) mm, net={net}")
                  .format(owner=v.owner_ref, x=v.position.x / 1e6, y=v.position.y / 1e6,
                          net=v.net_name))
        print("\n" + _("Tracks:"))
        for t in tracks:
            print(_("  track for {owner}: ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, "
                    "net={net}, width={w} mm")
                  .format(owner=t.owner_ref, sx=t.start.x / 1e6, sy=t.start.y / 1e6,
                          ex=t.end.x / 1e6, ey=t.end.y / 1e6, net=t.net_name, w=t.width_mm))
        print("\n" + _("(thermal via keepout is computed based on CURRENT component positions, "
                       "not the target ones — may slightly differ from the real run)"))
        print(_("(track collisions with other copper/components are NOT checked by this tool — "
                "rely on KiCad DRC after placement)"))
        print(_("(items later in the dependency chain above are ALSO planned from the CURRENT "
                "board, not the post-move board of their prerequisite — a real apply may place "
                "them differently; rerun without --dry-run for the true chained result)"))

    # ── Execution ───────────────────────────────────────────────────────────

    def _execute(self) -> None:
        executor = BatchExecutor(self.adapter, self.cfg, batch_size=self.batch_size)
        registry = PlacementRegistry(
            self.adapter,
            self.cfg.registry_path or registry_path_for_config(self.config_path),
        )
        track_registry = TrackRegistry(
            self.adapter,
            self.cfg.track_registry_path or track_registry_path_for_config(self.config_path),
        )

        # --- Phase 1: moves, one dependency-order item at a time ---
        self.planner.begin_planning()
        failed_refs: List[str] = []
        for idx, item in enumerate(self.items):
            if idx > 0:
                self.adapter.refresh_board()
            if item.anchor_ref is not None and item.anchor_ref in failed_refs:
                logger.warning(_("{label}: anchor {ref!r} failed to move earlier in this run — "
                                 "this item's placement is based on its OLD position")
                               .format(label=item.label, ref=item.anchor_ref))
            item_moves = self.planner.plan_item(item)
            logger.info(_("  {label}: {count} moves")
                        .format(label=item.label, count=len(item_moves)))
            item_failed = executor.execute_moves(
                item_moves,
                check_collisions=not self.no_collision_check,
                collision_margin_mm=self.collision_margin,
            )
            failed_refs.extend(item_failed)
        if failed_refs:
            logger.warning(_("Failed to move: {refs}").format(refs=sorted(set(failed_refs))))

        # --- Reload board ---
        logger.info(_("Reloading board data before planning vias..."))
        self.adapter.refresh_board()

        # --- Phase 2: vias ---
        all_vias = self.planner.plan_vias()
        vias_to_create = registry.reconcile(all_vias, known_anchor_ids=self.all_anchor_ids)
        logger.info(_("Planned vias: {total}, actually to create (registry filtered already "
                       "correctly placed): {to_create}")
                    .format(total=len(all_vias), to_create=len(vias_to_create)))
        logger.info(_("Applying vias..."))
        failed_vias = executor.execute_vias(vias_to_create, registry=registry)
        if failed_vias:
            logger.warning(_("Failed to create vias near: {refs}")
                           .format(refs=sorted(set(failed_vias))))

        # --- Phase 3: tracks ---
        all_tracks = self.planner.plan_tracks()
        tracks_to_create = track_registry.reconcile(all_tracks,
                                                     known_anchor_ids=self.all_anchor_ids)
        logger.info(_("Planned tracks: {total}, actually to create (registry filtered already "
                       "correctly placed): {to_create}")
                    .format(total=len(all_tracks), to_create=len(tracks_to_create)))
        logger.info(_("Applying tracks..."))
        failed_tracks = executor.execute_tracks(tracks_to_create, registry=track_registry)
        if failed_tracks:
            logger.warning(_("Failed to create tracks near: {refs}")
                           .format(refs=sorted(set(failed_tracks))))

        if not failed_refs and not failed_vias and not failed_tracks:
            logger.info(_("✅ All operations completed successfully"))
        else:
            logger.warning(_("⚠️ Some operations failed – check the log."))

    # ── Public entry point ──────────────────────────────────────────────────

    def run(self) -> None:
        """Run the full pipeline."""
        self._load_config()
        self._filter_config()
        self._connect_adapter()
        self._validate()
        self._resolve_order()
        self._create_planner()

        if self.dry_run:
            self._dry_run()
        else:
            self._execute()
