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

No changes to the planner/executor/registry engine or the YAML config
format — both are strictly additive.
"""
import dataclasses
from typing import Any, List, Optional

import yaml

from .config import ClonePlacement, Config, Rule
from .constants import DEFAULT_BATCH_SIZE, DEFAULT_TIMEOUT_MS

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
