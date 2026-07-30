# kicadspoke/author.py
"""
author.py — build ClonePlacement/Rule in real Python (loops, computed
values) instead of hand-writing repetitive YAML, where copy-paste mistakes
live (wrong nets: key, duplicate anchor_pad:, wrong anchor_sheet — all hit
live in one working session). Config/ClonePlacement/Rule (config/models.py)
are plain dataclasses already — this module adds nothing new to them, just
two ways to get a built list somewhere useful:

  (a) apply_config() — straight into the existing apply pipeline
      (kicadspoke_cli.cmd_apply already accepts a pre-built Config).
  (b) dump_clone_placements()/dump_rules() — serialize back to YAML, so
      generated subsystem files stay diffable/reviewable in git even when
      authored by a script.
  (c) cli_main() — the standard --apply/--dry-run entry point wiring (a)
      and (b) together, one place instead of every boards/*/scripts/*.py
      copy-pasting its own argparse block.

No changes to the planner/executor/registry engine or the YAML config
format — both are strictly additive.
"""
import dataclasses
import logging
import sys
from pathlib import Path
from typing import Any, Callable, List, Optional

import yaml
from kipy.errors import ApiError, ApiStatusCode

from .config import ClonePlacement, Config, Rule, load_config
from .constants import DEFAULT_BATCH_SIZE, DEFAULT_TIMEOUT_MS
from .exceptions import PlacerError

_MISSING = dataclasses.MISSING


def _default_for(f: "dataclasses.Field") -> Any:
    if f.default is not _MISSING:
        return f.default
    if f.default_factory is not _MISSING:  # type: ignore[misc]
        return f.default_factory()
    return _MISSING


def _prune_defaults(obj: Any) -> Any:
    """dataclass instance -> plain dict, dropping any field equal to its
    default (scalar default or default_factory() instance) — keeps
    generated YAML close to the hand-written minimal style already used in
    profiles/subsystems/*.yaml. Required fields (no default at all, e.g.
    ClonePlacement.name/origin_x_mm/origin_y_mm, Rule.net/spokes) are always
    kept regardless of value. Recurses into nested dataclasses and lists of
    them (only nesting that exists in these models: Rule.spokes -> List[ManualSpoke])."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result = {}
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            default = _default_for(f)
            if default is not _MISSING and value == default:
                continue
            if dataclasses.is_dataclass(value):
                result[f.name] = _prune_defaults(value)
            elif isinstance(value, list):
                result[f.name] = [_prune_defaults(v) if dataclasses.is_dataclass(v) else v
                                   for v in value]
            else:
                result[f.name] = value
        return result
    return obj


def dump_clone_placements(clones: List[ClonePlacement], path: str) -> None:
    """Writes {'clone_placements': [...]} to path — a file directly usable
    via include: (see kicadspoke/config/includes.py) or as a whole profile."""
    data = {"clone_placements": [_prune_defaults(c) for c in clones]}
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def dump_rules(rules: List[Rule], path: str) -> None:
    """Writes {'rules': [...]} to path — same include:-ready shape as
    dump_clone_placements."""
    data = {"rules": [_prune_defaults(r) for r in rules]}
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def dump_template(template_dict: dict, path: str) -> None:
    """Writes a template_extraction.extract_template_from_selection() result
    (already {name: {...}} shaped) straight to path, ready for templates_file.
    Same yaml.dump style as kicadspoke_cli.py's cmd_extract, minus its
    merge-into-existing-file behaviour: this always overwrites the whole
    file, matching dump_clone_placements/dump_rules — a script re-running
    extract for one subsystem should produce a clean, idempotent regeneration
    of its own dedicated file, not accumulate into a shared one. Use
    cmd_extract/the CLI directly if you want the merge behaviour instead."""
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(template_dict, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def apply_config(cfg: Config, config_path: str, *, dry_run: bool = False,
                  only: Optional[List[str]] = None, cluster: Optional[List[str]] = None,
                  timeout_ms: int = DEFAULT_TIMEOUT_MS, batch_size: int = DEFAULT_BATCH_SIZE,
                  no_collision_check: bool = False, collision_margin: float = 0.2) -> None:
    """Runs cfg through the exact same pipeline a YAML-driven `apply` run
    uses (kicadspoke_cli.cmd_apply already accepts a pre-built Config — this
    just builds the argparse.Namespace it expects).

    config_path is NOT cosmetic: when cfg.registry_path/cfg.track_registry_path
    are unset, cmd_apply derives them FROM IT (registry_path_for_config() /
    track_registry_path_for_config() in registry.py: '<config_path>.registry.json'
    next to it). A throwaway placeholder here would misfile or collide
    registries between unrelated scripted runs — exactly the class of bug
    fixed in this project before (registry prune granularity, thermal via
    duplication). Either point config_path at a real (possibly
    nonexistent-on-disk) path that identifies this run, or set
    cfg.registry_path/cfg.track_registry_path explicitly yourself.

    Deliberately does not re-run validation.run_all_checks() first: cmd_apply
    already does, before resolve_execution_order and before any board
    mutation — a separate pre-check here would only duplicate that work.
    """
    from argparse import Namespace
    # Local import: kicadspoke_cli.py is the project's CLI entry point, not
    # part of the kicadspoke package — importing it eagerly at module top
    # would require the project root on sys.path even for callers who never
    # call apply_config() at all. Same "import only when actually needed"
    # convention as via_planner.py's local import of registry.make_registry_key.
    from kicadspoke_cli import cmd_apply

    args = Namespace(
        config=config_path, dry_run=dry_run, only=only, cluster=cluster,
        timeout_ms=timeout_ms, batch_size=batch_size,
        no_collision_check=no_collision_check, collision_margin=collision_margin,
    )
    cmd_apply(args, cfg=cfg)


def cli_main(build_fn: Callable[[], List[ClonePlacement]], output_path: str,
             root_config_path: str, *, description: Optional[str] = None,
             argv: Optional[List[str]] = None) -> None:
    """Standard `if __name__ == "__main__":` body for a
    boards/<board>/scripts/*.py generator: parses --apply/--dry-run, writes
    build_fn()'s ClonePlacements to output_path via dump_clone_placements(),
    and — only with --apply — loads root_config_path and applies via
    apply_config(). One place for this boilerplate, so every subsystem
    script behaves identically instead of each one copy-pasting its own
    argparse block (single source of truth for the apply-gating logic).

    root_config_path (NOT output_path) is what gets loaded/applied — it's
    the one that carries schematic_dir/templates_file and (via include:)
    picks up output_path, and it's what registry identity is keyed off (see
    apply_config's own docstring) — it must be the SAME config every run.

    Without --apply (the default), this only ever writes output_path —
    never touches the live board. --dry-run only has an effect together
    with --apply (see apply_config).

    argv — for tests; None (default) reads sys.argv, like any CLI tool.
    """
    import argparse

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--apply", action="store_true",
                        help="also apply to the live board via kicadspoke.author.apply_config() "
                             "(connects to KiCad over IPC) after writing the YAML")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --apply, only print the plan, don't touch the board")
    parser.add_argument("--verbose", action="store_true",
                        help="DEBUG-level logging (default: INFO)")
    args = parser.parse_args(argv)

    # Local import, same convention as apply_config()'s import of cmd_apply:
    # kicadspoke_cli.py is the CLI entry point, not part of the package.
    # Without this, nothing configures a logging handler when a board script
    # is run directly (as opposed to through kicadspoke_cli.py's own main()),
    # so every logger.info/debug — including the role-resolver's ambiguity
    # narrowing cascade, exactly what you need to see when a role fails to
    # resolve — is silently dropped instead of printed.
    from kicadspoke.logging_setup import setup_logging
    setup_logging(verbose=args.verbose)

    # Mirrors kicadspoke_cli.py's own main() exception handling: without
    # this, a ValidationError/PlacerError from apply_config() (e.g. a role
    # ambiguity fatal — format_fatal_error's boxed message, already the
    # useful part) propagated as a raw Python traceback instead, burying the
    # actual message under a wall of stack frames — found live debugging
    # dac_pi_filter.py's role-resolution ambiguity.
    try:
        clones = build_fn()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        dump_clone_placements(clones, output_path)
        print(f"wrote {len(clones)} clone_placements to {output_path}")

        if args.apply:
            cfg, _ctx = load_config(root_config_path)
            apply_config(cfg, root_config_path, dry_run=args.dry_run)
    except PlacerError as e:
        logging.error(f"Error: {e}")
        sys.exit(1)
    except ApiError as e:
        if e.code == ApiStatusCode.AS_BUSY:
            logging.error(
                "KiCad is busy and cannot respond right now. Usually this means an unfinished "
                "tool is active in the GUI (dimensioning, interactive routing, move tool, etc.) — "
                "finish it (Esc or right-click -> Cancel) and run the command again. "
                "The board was not modified."
            )
        else:
            logging.error(f"KiCad returned API error: {e}")
        sys.exit(1)
    except Exception:
        logging.exception("Unexpected error")
        sys.exit(2)
