# kicadspoke/cli_extract.py
"""
extract / clone-extract CLI commands.

Extracted from kicadspoke_cli.py so the CLI entry point stays thin.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from kicadspoke.config.includes import resolve_includes
from kicadspoke.exceptions import PlacerError, check_unknown_keys
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.template_extraction import extract_template_from_selection, render_uncertain_comments
from kicadspoke.i18n import _


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
    on the selected profile itself, are merged in as a fallback.  For fields
    that are almost always the same across every profile in the file (e.g.
    every extract_profiles entry for one board writes into the same template
    file) — set it once at the root instead of repeating it per profile; a
    profile that genuinely needs a different value still overrides it as
    before, just by setting the field directly.  Empty/None (default) — no
    change from the old behaviour, used as-is by clone-extract.

    known_keys — if given, fatal on any key in the selected profile outside
    this set (see check_unknown_keys) — same protection clone_placements/
    rules already have.  A typo'd or wrong-separator key (e.g.
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


def cmd_extract(args) -> None:
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
