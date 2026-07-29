#!.venv/bin/python
"""
kicadspoke_cli.py — main entry point for KiCadSpoke.

Usage:
    python kicadspoke_cli.py apply config.yaml [--dry-run] [--timeout-ms 20000] [--batch-size 10]
    python kicadspoke_cli.py undo [--verbose]
"""

import argparse
import dataclasses
import difflib
import sys
import logging
import json
from typing import Dict, Any, List, Optional, Tuple
import yaml
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent))

# Translated/typographic text (em dashes, non-breaking hyphens, degree signs, ...)
# can't be encoded by legacy console codepages (e.g. Windows cp1251/cp866), which
# crashes the logging StreamHandler mid-run with UnicodeEncodeError. UTF-8 can
# encode any codepoint, so this removes the crash regardless of the terminal;
# whether it also *displays* correctly still depends on the terminal itself.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from kicadspoke.config import load_config, rule_effective_name, thermal_via_array_effective_name
from kicadspoke.config.includes import resolve_includes
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.placement.planner import PlacementPlanner
from kicadspoke.placement.dependency_order import resolve_execution_order
from kicadspoke.placement.services.clone_position_calculator import clone_anchor_id
from kicadspoke.placement.services.via_planner import thermal_anchor_id
from kicadspoke.placement.services.manual_position_calculator import rule_anchor_ids
from kicadspoke.placement.services.component_pool import _cluster_prefix_match
from kicadspoke.placement.executor import BatchExecutor
from kicadspoke.exceptions import PlacerError, check_unknown_keys
from kipy.errors import ApiError, ApiStatusCode
from kicadspoke.undo import undo_last_operation
from kicadspoke.validation import run_all_checks
from kicadspoke.registry import (PlacementRegistry, registry_path_for_config,
                                 TrackRegistry, track_registry_path_for_config)
from kicadspoke.template_extraction import extract_template_from_selection, render_uncertain_comments
from kicadspoke.constants import DEFAULT_TIMEOUT_MS, DEFAULT_BATCH_SIZE
from kicadspoke.i18n import _


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


class _ColorFormatter(logging.Formatter):
    """Wraps ERROR/CRITICAL lines in red, WARNING in yellow — ANSI escape
    codes, only when the console stream is a real terminal (use_color), so
    redirected/piped output never gets raw escape bytes. format_fatal_error()
    already marks each problem with '✗' — this makes the whole FATAL ERROR
    block visually impossible to miss instead of blending into a wall of
    INFO lines (found needed live: ambiguity errors from a board script were
    easy to scroll past in a long --apply log)."""
    _RED = "\033[91m"
    _YELLOW = "\033[93m"
    _RESET = "\033[0m"

    def __init__(self, fmt: str, use_color: bool):
        super().__init__(fmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not self._use_color:
            return message
        if record.levelno >= logging.ERROR:
            return f"{self._RED}{message}{self._RESET}"
        if record.levelno == logging.WARNING:
            return f"{self._YELLOW}{message}{self._RESET}"
        return message


def setup_logging(verbose: bool = False, log_file: str = None):
    """Configure logging: level and output to console and/or file."""
    level = logging.DEBUG if verbose else logging.INFO
    handlers = []
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    use_color = hasattr(console.stream, "isatty") and console.stream.isatty()
    console.setFormatter(_ColorFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s", use_color=use_color))
    handlers.append(console)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        handlers.append(file_handler)

    logging.basicConfig(level=logging.DEBUG, handlers=handlers)


def drop_disabled_rules(cfg, logger) -> None:
    """enabled: false always wins, dropped before --only/--cluster ever see
    it — enabled means "does not exist on the board right now", not
    "excluded from this particular run" (see Rule docstring in config/models.py).
    Pure cfg mutation, no adapter — kept separate from cmd_apply so it's
    unit‑testable without a live KiCad connection."""
    disabled_rules = [r for r in cfg.rules if not r.enabled]
    cfg.rules = [r for r in cfg.rules if r.enabled]
    for r in disabled_rules:
        logger.info(_("Rule {name!r} (net {net!r}): enabled=false, skipped entirely")
                    .format(name=rule_effective_name(r), net=r.net))


def drop_inactive_items(cfg, logger) -> None:
    """active: false — the inline, per-item counterpart of --only/--cluster
    (see Rule/ClonePlacement/ThermalViaArrayConfig.active docstrings in
    config/models.py). Unlike enabled: false (drop_disabled_rules above),
    this must run AFTER known_anchor_ids is computed in cmd_apply — an
    inactive item's via/tracks must still count as "known" so reconcile()
    protects them from pruning, it's just not (re)planned this run. Composes
    with --only/--cluster as a further AND-narrowing, same as those two
    compose with each other. Pure cfg mutation, no adapter.

    Added 2026-07-29: the user wanted to focus work on part of a rule/config
    without retyping --only every run, and without enabled: false pruning
    the parts left alone (see rule_anchor_ids/pad: fix earlier the same day —
    this reuses that same protection, just gated on active instead of on
    "was this item processed this run at all")."""
    active_clones = [c for c in cfg.clone_placements if c.active]
    dropped_clones = [c for c in cfg.clone_placements if not c.active]
    cfg.clone_placements = active_clones
    for c in dropped_clones:
        logger.info(_("ClonePlacement {name!r}: active=false, skipped this run "
                      "(existing via/tracks stay protected)").format(name=c.name))

    narrowed_rules = []
    for r in cfg.rules:
        if not r.active:
            logger.info(_("Rule {name!r}: active=false, skipped this run "
                          "(existing via/tracks stay protected)").format(name=rule_effective_name(r)))
            continue
        kept_spokes = [s for s in r.spokes if s.active]
        for s in r.spokes:
            if not s.active:
                logger.debug(_("Rule {name!r}: spoke on pad {pad} active=false, skipped this run")
                             .format(name=rule_effective_name(r), pad=s.pad))
        if kept_spokes:
            narrowed_rules.append(dataclasses.replace(r, spokes=kept_spokes))
        else:
            logger.info(_("Rule {name!r}: no active spokes left, skipped this run "
                          "(existing via/tracks stay protected)").format(name=rule_effective_name(r)))
    cfg.rules = narrowed_rules

    if cfg.thermal_via_array.enabled and not cfg.thermal_via_array.active:
        logger.info(_("thermal_via_array {name!r}: active=false, skipped this run "
                      "(existing vias stay protected)")
                    .format(name=thermal_via_array_effective_name(cfg.thermal_via_array)))
        cfg.thermal_via_array.enabled = False


def apply_only_filter(cfg, only_names: List[str], logger) -> None:
    """--only: whole-block selection by identity (rule name-or-net, clone_placement
    name, thermal_via_array name). sys.exit on unmatched names. Pure cfg mutation."""
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
            hint = _(" (maybe you meant {suggestion!r}?)").format(suggestion=suggestion[0]) if suggestion else ""
            lines.append(_("  {name!r} — not found among rules, clone_placements, or thermal_via_array{hint}")
                         .format(name=name, hint=hint))
        sys.exit(_("[error] --only: names not found:\n{lines}\nAvailable: {all}")
                 .format(lines="\n".join(lines), all=all_names))

    cfg.rules = matched_rules
    cfg.clone_placements = matched_clones
    if not thermal_matches:
        cfg.thermal_via_array.enabled = False
    logger.info(_("--only {requested}: rules={rules}, clone_placements={clones}, "
                  "thermal_via_array={thermal} (everything else is ignored in this run)")
                .format(requested=sorted(requested),
                        rules=[rule_effective_name(r) for r in matched_rules],
                        clones=[c.name for c in matched_clones],
                        thermal=_("yes") if thermal_matches else _("no")))


def apply_cluster_filter(cfg, cluster_paths: List[str], logger) -> None:
    """--cluster — a second, independent selection axis (physical instance /
    Cluster field, not name). Composes with --only via AND only, never OR:
    if you need "this OR that", just run apply twice — the registry makes
    repeated runs safe (already‑placed items are not duplicated). Pure cfg
    mutation, no adapter."""
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
            logger.debug(_("Rule {name!r}: no spokes match --cluster {paths}, rule dropped")
                         .format(name=rule_effective_name(r), paths=cluster_paths))

    if not narrowed_rules and not matched_clones and not thermal_matches:
        sys.exit(_("[error] --cluster {paths}: matched nothing among rules' spokes, "
                   "clone_placements, or thermal_via_array").format(paths=cluster_paths))

    cfg.rules = narrowed_rules
    cfg.clone_placements = matched_clones
    if not thermal_matches:
        cfg.thermal_via_array.enabled = False
    logger.info(_("--cluster {paths}: rules={rules} (spokes narrowed), "
                  "clone_placements={clones}, thermal_via_array={thermal}")
                .format(paths=cluster_paths,
                        rules=[rule_effective_name(r) for r in narrowed_rules],
                        clones=[c.name for c in matched_clones],
                        thermal=_("yes") if thermal_matches else _("no")))


def cmd_apply(args, cfg=None):
    """Main command: apply placement."""
    logger = logging.getLogger(__name__)
    if cfg is None:
        logger.info(_("Loading config: {config}").format(config=args.config))
        cfg = load_config(args.config)

    drop_disabled_rules(cfg, logger)

    # known_anchor_ids for registry.reconcile(): the FULL set (before --only/
    # --cluster narrow this particular run), excluding disabled clone_placements/
    # rules (enabled: false means "does not exist" — see drop_disabled_rules,
    # already applied above for cfg.rules). MUST be captured before
    # apply_only_filter/apply_cluster_filter mutate cfg.rules/cfg.clone_placements/
    # cfg.thermal_via_array.enabled below, or the whole point of known_anchor_ids
    # (protecting a temporarily filtered-out item's vias/tracks from being pruned
    # as "stale") silently does nothing (found 2026-07-28 while chasing orphaned
    # ldo_adj_subsystem tracks — was computed AFTER filtering, so a --only/
    # --cluster run would prune everything else's vias instead of preserving
    # them as documented).
    #
    # rule_anchor_ids(r) (2026-07-29): rules were missing from this set
    # entirely until now — a Rule has no single anchor_id like ClonePlacement
    # (clone_anchor_id), it's a GROUP of per-pad spokes, each registered under
    # its own 'pad:{pad}' key. Without this, --only/--cluster (or even just
    # temporarily narrowing which rules run) pruned a rule's via/tracks the
    # moment it was excluded from a run, --only/--cluster protection for
    # rule-based geometry never actually worked.
    all_anchor_ids = {clone_anchor_id(c) for c in cfg.clone_placements if c.enabled}
    for r in cfg.rules:
        all_anchor_ids |= rule_anchor_ids(r)
    if cfg.thermal_via_array.enabled:
        all_anchor_ids.add(thermal_anchor_id(cfg.thermal_via_array))

    # active: false (drop_inactive_items, 2026-07-29) is computed from — and
    # must run AFTER — all_anchor_ids above: it's the inline equivalent of
    # --only/--cluster (skip this run, protect existing geometry), not of
    # enabled: false (which is already excluded from all_anchor_ids by the
    # `if c.enabled`/drop_disabled_rules above).
    drop_inactive_items(cfg, logger)

    apply_only_filter(cfg, _split_comma_values(getattr(args, "only", None)), logger)
    apply_cluster_filter(cfg, _split_comma_values(getattr(args, "cluster", None)), logger)

    logger.info(_("Connecting to KiCad (timeout {timeout} ms)").format(timeout=args.timeout_ms))
    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()

    run_all_checks(adapter, cfg)

    logger.info(_("Resolving item execution order (dependency chain — see dependency_order.py)..."))
    items = resolve_execution_order(adapter, cfg)
    logger.info(_("Execution order: {order}").format(order=" -> ".join(it.label for it in items)))

    planner = PlacementPlanner(adapter, cfg)

    if args.dry_run:
        moves = planner.plan_items(items)
        vias = planner.plan_vias()
        tracks = planner.plan_tracks()
        print("\n=== DRY RUN ===")
        print(_("Order: {order}").format(order=" -> ".join(it.label for it in items)))
        print(_("Moves:"))
        for m in moves:
            print(_("  {ref}: ({x:.3f}, {y:.3f}) mm, angle={angle:.1f}°")
                  .format(ref=m.ref, x=m.position.x/1e6, y=m.position.y/1e6, angle=m.angle.degrees))
        print("\n" + _("Vias:"))
        for v in vias:
            print(_("  via for {owner}: ({x:.3f}, {y:.3f}) mm, net={net}")
                  .format(owner=v.owner_ref, x=v.position.x/1e6, y=v.position.y/1e6, net=v.net_name))
        print("\n" + _("Tracks:"))
        for t in tracks:
            print(_("  track for {owner}: ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, net={net}, width={w} mm")
                  .format(owner=t.owner_ref, sx=t.start.x/1e6, sy=t.start.y/1e6,
                          ex=t.end.x/1e6, ey=t.end.y/1e6, net=t.net_name, w=t.width_mm))
        print("\n" + _("(thermal via keepout is computed based on CURRENT component positions, "
                      "not the target ones — may slightly differ from the real run)"))
        print(_("(track collisions with other copper/components are NOT checked by this tool — "
                "rely on KiCad DRC after placement)"))
        print(_("(items later in the dependency chain above are ALSO planned from the CURRENT "
                "board, not the post-move board of their prerequisite — a real apply may place "
                "them differently; rerun without --dry-run for the true chained result)"))
        return

    executor = BatchExecutor(adapter, cfg, batch_size=args.batch_size)
    registry = PlacementRegistry(adapter, cfg.registry_path or registry_path_for_config(args.config))
    track_registry = TrackRegistry(adapter, cfg.track_registry_path or track_registry_path_for_config(args.config))

    # --- Phase 1: moves, one dependency-order item at a time ---
    # Each item is planned against the board as it is RIGHT NOW (refreshed
    # right after the previous item's moves were committed), so an item
    # anchored on something an EARLIER item in this run is about to move sees
    # its real, post-move position — not a stale pre-run snapshot. See
    # dependency_order.py for why this matters (found via p5v_led_spoke).
    planner.begin_planning()
    failed_refs = []
    for idx, item in enumerate(items):
        if idx > 0:
            adapter.refresh_board()
        if item.anchor_ref is not None and item.anchor_ref in failed_refs:
            logger.warning(_("{label}: anchor {ref!r} failed to move earlier in this run — "
                             "this item's placement is based on its OLD position")
                           .format(label=item.label, ref=item.anchor_ref))
        item_moves = planner.plan_item(item)
        logger.info(_("  {label}: {count} moves").format(label=item.label, count=len(item_moves)))
        item_failed = executor.execute_moves(
            item_moves,
            check_collisions=not args.no_collision_check,
            collision_margin_mm=args.collision_margin,
        )
        failed_refs.extend(item_failed)
    if failed_refs:
        logger.warning(_("Failed to move: {refs}").format(refs=sorted(set(failed_refs))))

    # --- Reload board ---
    logger.info(_("Reloading board data before planning vias..."))
    adapter.refresh_board()

    # --- Phase 2: vias (via/track CREATION does not need per-item ordering —
    # only anchor resolution does; positions were already fixed above, per
    # item, against the correct board state) ---
    all_vias = planner.plan_vias()
    vias_to_create = registry.reconcile(all_vias, known_anchor_ids=all_anchor_ids)
    logger.info(_("Planned vias: {total}, actually to create (registry filtered already correctly placed): {to_create}")
                .format(total=len(all_vias), to_create=len(vias_to_create)))
    logger.info(_("Applying vias..."))
    failed_vias = executor.execute_vias(vias_to_create, registry=registry)
    if failed_vias:
        logger.warning(_("Failed to create vias near: {refs}").format(refs=sorted(set(failed_vias))))

    # --- Phase 3: tracks ---
    all_tracks = planner.plan_tracks()
    tracks_to_create = track_registry.reconcile(all_tracks, known_anchor_ids=all_anchor_ids)
    logger.info(_("Planned tracks: {total}, actually to create (registry filtered already correctly placed): {to_create}")
                .format(total=len(all_tracks), to_create=len(tracks_to_create)))
    logger.info(_("Applying tracks..."))
    failed_tracks = executor.execute_tracks(tracks_to_create, registry=track_registry)
    if failed_tracks:
        logger.warning(_("Failed to create tracks near: {refs}").format(refs=sorted(set(failed_tracks))))

    if not failed_refs and not failed_vias and not failed_tracks:
        logger.info(_("✅ All operations completed successfully"))
    else:
        logger.warning(_("⚠️ Some operations failed – check the log."))


# extract_profiles: / clone_profiles: known keys — see load_profile's
# known_keys param. Field names read from the profile dict: cmd_extract's
# --profile branch (name/output/params/net_template/net_template_role/
# origin_by_via_net/origin_by_component_role/origin_by_component_pad) and
# clone-extract's --profile branch (net/pcb/channel/output).
_EXTRACT_PROFILE_KNOWN_KEYS = {
    'name', 'output', 'params', 'net_template', 'net_template_role',
    'origin_by_via_net', 'origin_by_component_role', 'origin_by_component_pad',
}
_CLONE_EXTRACT_PROFILE_KNOWN_KEYS = {'net', 'pcb', 'channel', 'output'}


def load_profile(profiles_path: str, top_key: str, profile_name: str,
                  root_defaults: Optional[List[str]] = None,
                  known_keys: Optional[set] = None) -> Dict[str, Any]:
    """
    Common loader for named CLI profiles (for extract and clone-extract).
    top_key is different for each command (extract_profiles / clone_profiles).

    root_defaults — field names that, if set at the ROOT of the file (sibling
    to top_key, e.g. output: next to extract_profiles:) and not already set
    on the selected profile itself, are merged in as a fallback. For fields
    that are almost always the same across every profile in the file (e.g.
    every extract_profiles entry for one board writes into the same template
    file) — set it once at the root instead of repeating it per profile; a
    profile that genuinely needs a different value still overrides it as
    before, just by setting the field directly. Empty/None (default) — no
    change from the old behaviour, used as-is by clone-extract.

    known_keys — if given, fatal on any key in the selected profile outside
    this set (see check_unknown_keys) — same protection clone_placements/
    rules already have. A typo'd or wrong-separator key (e.g.
    'origin-by-via-net' instead of 'origin_by_via_net') was previously
    silently ignored: found live on boards/3ch-awg-tia, the origin quietly
    fell back to the selection bbox instead of the intended via.
    """
    p = Path(profiles_path)
    if not p.exists():
        sys.exit(_("[error] profiles file {path!r} not found").format(path=profiles_path))
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data = resolve_includes(str(p), data)
    profiles = data.get(top_key, {})
    if profile_name not in profiles:
        available = list(profiles.keys())
        sys.exit(_("[error] profile {name!r} not found in {top_key!r} of file {path!r}. Available: {avail}")
                 .format(name=profile_name, top_key=top_key, path=profiles_path, avail=available))
    prof = dict(profiles[profile_name])
    for field in (root_defaults or []):
        if field not in prof and field in data:
            prof[field] = data[field]
    if known_keys is not None:
        check_unknown_keys(prof, known_keys,
                           _("unknown fields in {top_key} {name!r} of {path!r}")
                           .format(top_key=top_key, name=profile_name, path=profiles_path))
    return prof


def cmd_extract(args):
    """Extract a spoke template from the current selection on the board."""
    logger = logging.getLogger(__name__)
    logger.info(_("Connecting to KiCad (timeout {timeout} ms)").format(timeout=args.timeout_ms))
    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()

    direct_args_given = bool(args.name or args.output or args.param or args.net_template
                             or args.net_template_role
                             or args.origin_by_via_net or args.origin_by_component_role
                             or args.origin_by_component_pad)
    if args.profile and direct_args_given:
        sys.exit(_("[error] --profile cannot be combined with --name/--output/--param/--net-template/"
                   "--net-template-role/--origin-by-*: either all from profile or all as explicit flags, "
                   "not mixed."))

    if args.profile:
        if not args.profiles:
            sys.exit(_("[error] --profile given without --profiles (profiles file)"))
        prof = load_profile(args.profiles, "extract_profiles", args.profile, root_defaults=["output"],
                            known_keys=_EXTRACT_PROFILE_KNOWN_KEYS)
        if "output" not in prof:
            sys.exit(_("[error] profile {profile!r} missing required field {field!r}")
                     .format(profile=args.profile, field="output"))
        # name: defaults to the profile's own key — only set it explicitly when
        # the template name must differ from the profile name (e.g. several
        # profiles feeding the same shared template, like cap_pair_standard).
        name = prof.get("name", args.profile)
        output = prof["output"]
        params = dict(prof.get("params", {}) or {})
        net_template_map = dict(prof.get("net_template", {}) or {})
        net_template_role = dict(prof.get("net_template_role", {}) or {})
        origin_via_net = prof.get("origin_by_via_net")
        origin_component_role = prof.get("origin_by_component_role")
        origin_component_pad = prof.get("origin_by_component_pad")
        logger.info(_("Profile {profile!r} from {profiles}: name={name}, output={output}")
                    .format(profile=args.profile, profiles=args.profiles, name=name, output=output))
    else:
        name = args.name
        output = args.output
        if not name:
            try:
                name = input(_("Template name (key under templates:): ")).strip()
            except EOFError:
                name = ""
        if not name or not output:
            sys.exit(_("[error] need --name and --output (or --profiles/--profile instead)"))
        params = {}
        for item in (args.param or []):
            if "=" not in item:
                logger.error(_("--param {item!r} — need format KEY=VALUE").format(item=item))
                sys.exit(1)
            k, v = item.split("=", 1)
            params[k] = v

        net_template_map = {}
        for item in (args.net_template or []):
            if "=" not in item:
                logger.error(_("--net-template {item!r} — need format LITERAL=PATTERN").format(item=item))
                sys.exit(1)
            literal, pattern = item.split("=", 1)
            net_template_map[literal] = pattern

        net_template_role = {}
        for item in (args.net_template_role or []):
            if "=" not in item:
                logger.error(_("--net-template-role {item!r} — need format ROLE=LITERAL").format(item=item))
                sys.exit(1)
            role_key, literal = item.split("=", 1)
            net_template_role[role_key] = literal
        origin_via_net = args.origin_by_via_net
        origin_component_role = args.origin_by_component_role
        origin_component_pad = args.origin_by_component_pad

    if origin_component_pad and not origin_component_role:
        sys.exit(_("[error] --origin-by-component-pad without --origin-by-component-role — "
                   "you can only refine a pad for a role that you first specify"))

    annotations: List[Tuple[str, str, str]] = []
    template_dict = extract_template_from_selection(
        adapter, name, params=params, net_template_map=net_template_map,
        origin_via_net=origin_via_net,
        origin_component_role=origin_component_role,
        origin_component_pad=origin_component_pad,
        net_template_role=net_template_role,
        annotations=annotations,
    )

    output_path = Path(output)
    is_json = output_path.suffix.lower() == '.json'
    existing = {}
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            existing = (json.load(f) if is_json else yaml.safe_load(f)) or {}
        if name in existing:
            logger.warning(_("Template {name!r} already exists in {output} — will be overwritten")
                           .format(name=name, output=output_path))

    existing.update(template_dict)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        if is_json:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        else:
            text = yaml.dump(existing, allow_unicode=True, sort_keys=False, default_flow_style=False)
            if annotations:
                text = render_uncertain_comments(text, name, annotations)
            f.write(text)

    logger.info(_("✅ Template {name!r} written to {output}").format(name=name, output=output_path))


def cmd_undo(args):
    """Undo the last operation."""
    logger = logging.getLogger(__name__)
    log_dir = Path("logs")
    if not log_dir.exists():
        logger.error(_("logs directory not found."))
        return

    files = sorted(log_dir.glob("operation_*.json"), key=lambda p: p.stat().st_ctime)
    if not files:
        logger.error(_("No operation files to undo."))
        return

    last_file = files[-1]
    logger.info(_("Undoing operation from {file}").format(file=last_file.name))
    success = undo_last_operation(last_file)
    if success:
        logger.info(_("✅ Operation successfully undone."))
    else:
        logger.error(_("❌ Failed to undo operation."))


def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ['apply', 'undo', 'extract', 'clone-extract']:
        sys.argv.insert(1, 'apply')

    parser = argparse.ArgumentParser(
        description=_("KiCad Decap Placer – capacitor placement (manual strategy)"),
        epilog=_("Example: kicadspoke_cli.py config.yaml --dry-run")
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help=_("Subcommand"))

    apply_parser = subparsers.add_parser("apply", help=_("Apply placement"))
    apply_parser.add_argument("config", help=_("YAML configuration file"))
    apply_parser.add_argument("--dry-run", action="store_true", help=_("Only print the plan, do not apply"))
    apply_parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help=_("IPC timeout in ms"))
    apply_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=_("Batch size for commits"))
    apply_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    apply_parser.add_argument("--log-file", help=_("File to save logs"))
    apply_parser.add_argument("--no-collision-check", action="store_true", help=_("Disable collision checking"))
    apply_parser.add_argument("--collision-margin", type=float, default=0.2, help=_("Extra clearance for collision check in mm"))
    apply_parser.add_argument("--only", action="append", metavar="NAME",
                              help=_("Process only rules/clone_placements/thermal_via_array with this "
                                     "identity (rule name if set, else its net; clone_placement/"
                                     "thermal_via_array name). Repeatable and/or comma-separated "
                                     "(--only a,b --only c). Everything else is ignored in this run."))
    apply_parser.add_argument("--cluster", action="append", metavar="PATH",
                              help=_("Process only spokes/clone_placements/thermal_via_array whose "
                                     "Cluster (anchor_cluster / spoke cluster) matches this path or "
                                     "prefix (segment-wise, e.g. 'Channel_0' also matches "
                                     "'Channel_0/DAC_OA'). Repeatable and/or comma-separated. "
                                     "Combines with --only via AND (run apply twice for OR)."))

    undo_parser = subparsers.add_parser("undo", help=_("Undo last operation"))
    undo_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    undo_parser.add_argument("--log-file", help=_("File to save logs"))

    clone_extract = subparsers.add_parser(
        "clone-extract",
        help=_("Snapshot a channel to YAML (file‑based cloner, no IPC)")
    )
    clone_extract.add_argument("--net", help=_("Path to .net file"))
    clone_extract.add_argument("--pcb", help=_("Path to .kicad_pcb file"))
    clone_extract.add_argument("--channel", help=_("Channel name, e.g. Channel_0"))
    clone_extract.add_argument("--output", help=_("YAML snapshot file"))
    clone_extract.add_argument("--profiles", metavar="FILE",
                               help=_("YAML file with named profiles for clone-extract"))
    clone_extract.add_argument("--profile", metavar="NAME",
                               help=_("Take net/pcb/channel/output from profile NAME in --profiles file "
                                      "(cannot combine with explicit flags)"))
    clone_extract.add_argument("-v", "--verbose", action="store_true", help=_("Verbose output"))

    extract_parser = subparsers.add_parser("extract", help=_("Extract spoke template from current selection"))
    extract_parser.add_argument("--name", help=_("Template name (key in templates:)"))
    extract_parser.add_argument("--output", help=_("Output YAML/JSON file"))
    extract_parser.add_argument("--profiles", metavar="FILE",
                                help=_("YAML file with named profiles for extract"))
    extract_parser.add_argument("--profile", metavar="NAME",
                                help=_("Take name/output/param/net-template/origin-by-* from profile NAME "
                                       "in --profiles file (cannot combine with explicit flags)"))
    extract_parser.add_argument("--timeout-ms", type=int, default=20000, help=_("IPC timeout in ms"))
    extract_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    extract_parser.add_argument("--log-file", help=_("File to save logs"))
    extract_parser.add_argument("--param", action="append", metavar="KEY=VALUE",
                                help=_("Parameter for --net-template verification (e.g. channel=1); "
                                       "can be repeated; not written to template, only round-trip check"))
    extract_parser.add_argument("--net-template", action="append", metavar="LITERAL=PATTERN",
                                help=_("Mapping real net -> pattern with {placeholder} "
                                       "(e.g. 'DAC1_DB1=DAC{channel}_DB1'); can be repeated; "
                                       "fills net_template for roles and parametrizes via.net at extraction"))
    extract_parser.add_argument("--net-template-role", action="append", metavar="ROLE=LITERAL",
                                help=_("For components with multiple nets from --net-template on pads "
                                       "(ferrite/inductor/fuse between two rails) – explicitly tells "
                                       "which net is the role's net_template (e.g. 'PI_FILTER_FB=+5V_DIRTY'); "
                                       "without this such roles get empty net_template and need manual edit. "
                                       "Fatal if the role does not actually have that net on its pads, "
                                       "or if the literal is not registered in --net-template/params."))
    origin_group = extract_parser.add_mutually_exclusive_group()
    origin_group.add_argument("--origin-by-via-net", metavar="NET",
                              help=_("Template origin — position of via on this net (instead of bbox); "
                                     "fatal if no such via in selection or more than one"))
    origin_group.add_argument("--origin-by-component-role", metavar="ROLE",
                              help=_("Template origin — position of component with this role "
                                     "(instead of bbox); fatal if role not found in selection"))
    extract_parser.add_argument("--origin-by-component-pad", metavar="PAD",
                                help=_("Refine --origin-by-component-role: origin is the position of "
                                       "the specific pad of that component, not its centre. "
                                       "Fatal without --origin-by-component-role."))

    args = parser.parse_args()

    # Load config early (only for apply) to pick up log_file from config if --log-file not given.
    cfg = None
    if args.command == "apply":
        try:
            cfg = load_config(args.config)
        except Exception:
            cfg = None

    log_file = getattr(args, "log_file", None) or (cfg.log_file if cfg else None)
    setup_logging(verbose=getattr(args, "verbose", False), log_file=log_file)

    try:
        if args.command == "apply":
            cmd_apply(args, cfg=cfg)
        elif args.command == "undo":
            cmd_undo(args)
        elif args.command == "clone-extract":
            direct_given = bool(args.net or args.pcb or args.channel or args.output)
            if args.profile and direct_given:
                sys.exit(_("[error] --profile cannot be combined with --net/--pcb/--channel/--output"))
            if args.profile:
                if not args.profiles:
                    sys.exit(_("[error] --profile given without --profiles (profiles file)"))
                prof = load_profile(args.profiles, "clone_profiles", args.profile,
                                    known_keys=_CLONE_EXTRACT_PROFILE_KNOWN_KEYS)
                for required in ("net", "pcb", "channel", "output"):
                    if required not in prof:
                        sys.exit(_("[error] profile {profile!r} missing required field {field!r}")
                                 .format(profile=args.profile, field=required))
                net_path, pcb_path, channel, output = prof["net"], prof["pcb"], prof["channel"], prof["output"]
            else:
                if not (args.net and args.pcb and args.channel and args.output):
                    sys.exit(_("[error] need --net/--pcb/--channel/--output (or --profiles/--profile)"))
                net_path, pcb_path, channel, output = args.net, args.pcb, args.channel, args.output
            from kicadspoke.cloner.extract import extract_channel
            d = extract_channel(net_path, pcb_path, channel, output)
            s = d['summary']
            print(_("[{channel}] footprints: {fp}, segments: {seg}, vias: {vias} -> {output}")
                  .format(channel=channel, fp=s['footprints'], seg=s['segments'],
                          vias=s['vias'], output=output))
        elif args.command == "extract":
            cmd_extract(args)
        else:
            parser.print_help()
            sys.exit(1)
    except PlacerError as e:
        logging.error(_("Error: {e}").format(e=e))
        sys.exit(1)
    except ApiError as e:
        if e.code == ApiStatusCode.AS_BUSY:
            logging.error(
                _("KiCad is busy and cannot respond right now. Usually this means an unfinished "
                  "tool is active in the GUI (dimensioning, interactive routing, move tool, etc.) — "
                  "finish it (Esc or right-click -> Cancel) and run the command again. "
                  "The board was not modified.")
            )
        else:
            logging.error(_("KiCad returned API error: {e}").format(e=e))
        sys.exit(1)
    except Exception as e:
        logging.exception(_("Unexpected error"))
        sys.exit(2)


if __name__ == "__main__":
    main()