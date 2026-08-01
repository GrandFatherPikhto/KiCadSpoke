# kicadstamp/apply_pipeline.py
"""
apply_pipeline.py — orchestrates the full ``apply`` pipeline.

Extracted from kicadstamp_cli.py's cmd_apply() to separate pipeline orchestration
from CLI argument parsing.  The pipeline is:

    load config
      -> compute known_anchor_ids (for registry protection)
      -> filter (retired, skip, --only, --cluster)
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
from typing import Dict, Any, List, Optional, Set

from kipy.errors import ApiError, ApiStatusCode

from .config import load_config, rule_effective_name, thermal_via_array_effective_name
from .runtime_context import RuntimeContext
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
    """retired: true always wins, dropped before --only/--cluster ever see
    it — retired means "does not exist on the board right now", not
    "excluded from this particular run" (see Rule docstring in config/models.py).
    Pure cfg mutation, no adapter — kept separate so it's unit‑testable without
    a live KiCad connection."""
    l = _logger or logger
    disabled_rules = [r for r in cfg.rules if r.retired]
    cfg.rules = [r for r in cfg.rules if not r.retired]
    for r in disabled_rules:
        l.info(_("Rule {name!r} (net {net!r}): retired=true, skipped entirely")
               .format(name=rule_effective_name(r), net=r.net))


def drop_inactive_items(cfg, _logger=None) -> None:
    """skip: true — the inline, per-item counterpart of --only/--cluster
    (see Rule/ClonePlacement/ThermalViaArrayConfig.skip docstrings in
    config/models.py). Unlike retired: true (drop_disabled_rules above),
    this must run AFTER known_anchor_ids is computed — a skipped item's
    via/tracks must still count as "known" so reconcile() protects them from
    pruning, it's just not (re)planned this run. Composes with --only/--cluster
    as a further AND-narrowing. Pure cfg mutation, no adapter."""
    l = _logger or logger
    kept_clones = [c for c in cfg.clone_placements if not c.skip]
    dropped_clones = [c for c in cfg.clone_placements if c.skip]
    cfg.clone_placements = kept_clones
    for c in dropped_clones:
        l.info(_("ClonePlacement {name!r}: skip=true, skipped this run "
                  "(existing via/tracks stay protected)").format(name=c.name))

    narrowed_rules = []
    for r in cfg.rules:
        if r.skip:
            l.info(_("Rule {name!r}: skip=true, skipped this run "
                      "(existing via/tracks stay protected)").format(name=rule_effective_name(r)))
            continue
        kept_spokes = [s for s in r.spokes if not s.skip]
        for s in r.spokes:
            if s.skip:
                l.debug(_("Rule {name!r}: spoke on pad {pad} skip=true, skipped this run")
                         .format(name=rule_effective_name(r), pad=s.pad))
        if kept_spokes:
            narrowed_rules.append(dataclasses.replace(r, spokes=kept_spokes))
        else:
            l.info(_("Rule {name!r}: no non-skipped spokes left, skipped this run "
                      "(existing via/tracks stay protected)").format(name=rule_effective_name(r)))
    cfg.rules = narrowed_rules

    if not cfg.thermal_via_array.retired and cfg.thermal_via_array.skip:
        l.info(_("thermal_via_array {name!r}: skip=true, skipped this run "
                  "(existing vias stay protected)")
               .format(name=thermal_via_array_effective_name(cfg.thermal_via_array)))
        cfg.thermal_via_array.retired = True


def apply_only_filter(cfg, only_names: List[str], _logger=None) -> None:
    """--only: whole-block selection by identity (rule name-or-net, clone_placement
    name, thermal_via_array name). Raises PlacerError on unmatched names.
    Pure cfg mutation."""
    l = _logger or logger
    if not only_names:
        return
    requested = set(only_names)
    matched_rules = [r for r in cfg.rules if rule_effective_name(r) in requested]
    matched_clones = [c for c in cfg.clone_placements if c.name in requested]
    thermal_matches = (not cfg.thermal_via_array.retired and
                       thermal_via_array_effective_name(cfg.thermal_via_array) in requested)

    found_names = ({rule_effective_name(r) for r in matched_rules}
                   | {c.name for c in matched_clones}
                   | ({thermal_via_array_effective_name(cfg.thermal_via_array)}
                      if thermal_matches else set()))
    missing = requested - found_names
    if missing:
        tva_name = (thermal_via_array_effective_name(cfg.thermal_via_array)
                    if not cfg.thermal_via_array.retired else None)
        all_names = sorted(
            {rule_effective_name(r) for r in cfg.rules}
            | {c.name for c in cfg.clone_placements}
            | ({tva_name} if tva_name is not None else set())
        )
        lines = []
        for name in sorted(missing):
            suggestion = difflib.get_close_matches(name, all_names, n=1)
            hint = (_(" (maybe you meant {suggestion!r}?)").format(suggestion=suggestion[0])
                    if suggestion else "")
            lines.append(_("  {name!r} — not found among rules, clone_placements, "
                           "or thermal_via_array{hint}")
                         .format(name=name, hint=hint))
        raise PlacerError(_("[error] --only: names not found:\n{lines}\nAvailable: {all}")
                          .format(lines="\n".join(lines), all=all_names))

    cfg.rules = matched_rules
    cfg.clone_placements = matched_clones
    if not thermal_matches:
        cfg.thermal_via_array.retired = True
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
    thermal_matches = (not cfg.thermal_via_array.retired and
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
        raise PlacerError(_("[error] --cluster {paths}: matched nothing among rules' spokes, "
                            "clone_placements, or thermal_via_array").format(paths=cluster_paths))

    cfg.rules = narrowed_rules
    cfg.clone_placements = matched_clones
    if not thermal_matches:
        cfg.thermal_via_array.retired = True
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
    ids = {clone_anchor_id(c) for c in cfg.clone_placements if not c.retired}
    for r in cfg.rules:
        ids |= rule_anchor_ids(r)
    if not cfg.thermal_via_array.retired:
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
        preloaded_ctx=None,
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
        self.ctx: Optional[RuntimeContext] = preloaded_ctx
        self.adapter: Optional[KiCadBoardAdapter] = None
        self.planner: Optional[PlacementPlanner] = None
        self.items = None
        self.all_anchor_ids: Set[str] = set()

    # ── Pipeline steps ──────────────────────────────────────────────────────

    def _load_config(self) -> None:
        if self.cfg is None:
            logger.info(_("Loading config: {config}").format(config=self.config_path))
            self.cfg, self.ctx = load_config(self.config_path)

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
        run_all_checks(self.adapter, self.cfg, sheet_names=self.ctx.sheet_names if self.ctx else {})

    def _resolve_order(self) -> None:
        logger.info(_("Resolving item execution order (dependency chain — see dependency_order.py)..."))
        _sn = self.ctx.sheet_names if self.ctx else {}
        self.items = resolve_execution_order(self.adapter, self.cfg, sheet_names=_sn)
        logger.info(_("Execution order: {order}")
                    .format(order=" -> ".join(it.label for it in self.items)))

    def _create_planner(self) -> None:
        _sn = self.ctx.sheet_names if self.ctx else {}
        self.planner = PlacementPlanner(self.adapter, self.cfg, sheet_names=_sn)

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


# ── Module-level convenience entry point ──────────────────────────────────


@dataclasses.dataclass
class RunOptions:
    """Typed replacement for the ad-hoc ``argparse.Namespace`` that
    :func:`cmd_apply` used to read.

    Carries every knob the :class:`ApplyPipeline` needs for one apply run, so
    library callers (:mod:`kicadstamp.author`) can build a fully-typed option
    object instead of synthesizing a fake ``argparse.Namespace``. Defaults
    mirror :class:`ApplyPipeline`'s own keyword defaults (``timeout_ms=20000``,
    ``batch_size=10``, ``collision_margin=0.2``).
    """

    config_path: str
    timeout_ms: int = 20000
    batch_size: int = 10
    dry_run: bool = False
    no_selection: bool = False
    no_collision_check: bool = False
    collision_margin: float = 0.2
    only: Optional[List[str]] = None
    cluster: Optional[List[str]] = None


def run_apply(options: RunOptions, cfg=None, ctx=None) -> None:
    """Run the apply pipeline for a fully-typed :class:`RunOptions`.

    *options* — the typed run configuration (:class:`RunOptions`).
    *cfg*     — pre-loaded :class:`~kicadstamp.config.Config` (skips config
                load when given).
    *ctx*     — pre-loaded :class:`RuntimeContext` paired with *cfg*.

    This is the library-facing entry point; the CLI wrapper :func:`cmd_apply`
    and :func:`kicadstamp.author.apply_config` both funnel into it.
    """
    pipeline = ApplyPipeline(
        config_path=options.config_path,
        timeout_ms=options.timeout_ms,
        batch_size=options.batch_size,
        dry_run=options.dry_run,
        no_selection=options.no_selection,
        no_collision_check=options.no_collision_check,
        collision_margin=options.collision_margin,
        only=options.only,
        cluster=options.cluster,
        preloaded_cfg=cfg,
        preloaded_ctx=ctx,
    )
    pipeline.run()


def cmd_apply(args, cfg=None, ctx=None):
    """Thin shim: build a :class:`RunOptions` from an ``argparse.Namespace``
    and hand it to :func:`run_apply`.

    Kept only so :mod:`kicadstamp_cli` can keep calling the same name. New
    callers should use :func:`run_apply` with a real :class:`RunOptions`
    instead of going through a synthetic Namespace.

    Extracted from ``kicadstamp_cli.py`` to break the import cycle:
    ``author.py → kicadstamp_cli.py → author.py``.
    """
    options = RunOptions(
        config_path=args.config,
        timeout_ms=args.timeout_ms,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        no_selection=getattr(args, "no_selection", False),
        no_collision_check=args.no_collision_check,
        collision_margin=args.collision_margin,
        only=getattr(args, "only", None),
        cluster=getattr(args, "cluster", None),
    )
    run_apply(options, cfg=cfg, ctx=ctx)
