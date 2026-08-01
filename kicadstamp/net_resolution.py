# kicadstamp/net_resolution.py
"""
net_resolution.py — three‑layer net name resolution for cloned cells
(TemplatePlacer), in order of increasing specificity:

  1. Literal ("GND") — as‑is, no placeholders.
  2. Placeholder ("DAC{channel}_DB1") — substituted from params
     (str.format), written manually into the cell at extraction/
     editing time — NOT automatically derived from any pattern.
  3. net_overrides — applied ON TOP of the result of steps 1‑2, by the
     resolved (already substituted) name — for point exceptions like
     hierarchical paths (/STM32F4xx/BOOT0) that do not fit even into
     parametrisation.

No automatic guessing anywhere — both mechanisms (params and net_overrides)
require explicit, hand‑written configuration.
"""
from typing import Any
from .exceptions import ValidationError, format_fatal_error
from .i18n import _


def resolve_placeholder(template: str, params: dict[str, Any], what: str = "value") -> str:
    """
    Generic {placeholder} substitution from params (str.format) — the engine
    underneath resolve_net, also reused as-is for ClonePlacement.anchor_sheet
    (see clone_role_resolver.resolve_anchor_by_role): the Role field is shared
    across every instance of a reused sheet (e.g. channel.kicad_sch used 3×),
    but the sheet NAME differs per instance (Channel_0/Channel_1/...), so a
    clone_placement parametrized with params: {channel: N} needs
    anchor_sheet: 'Channel_{channel}' to actually narrow per‑instance — a
    literal anchor_sheet would have to be repeated, unparametrized, in every
    clone_placement copy.
    """
    try:
        return template.format(**params)
    except KeyError as e:
        raise ValidationError(format_fatal_error(
            _("{what} {template!r} has a placeholder with no parameter").format(what=what, template=template),
            [_("missing parameter {param} — add it to params of this clone_placement, "
               "or remove the placeholder").format(param=e)]
        ))


def resolve_net(net_template: str, params: dict[str, Any], net_overrides: dict[str, str]) -> str:
    """
    net_template — net name as written in the cell (TemplateVia.net),
    possibly with {placeholder}. params — substitution values (from
    ClonePlacement.params). net_overrides — point override of the final name
    (from ClonePlacement.net_overrides).
    """
    resolved = resolve_placeholder(net_template, params, what="net")
    return net_overrides.get(resolved, resolved)


def parametrize_net(literal_net: str, net_template_map: dict[str, str],
                     params: dict[str, Any]) -> str:
    """
    Reverse operation of resolve_net — for extract, not for apply.

    literal_net — real net name read from the board (v.net.name).
    net_template_map — explicit, hand‑written mapping literal->pattern
    (e.g. {"DAC1_DB1": "DAC{channel}_DB1"}), set once at extract time via
    --net-template. params — the same params that will later resolve the pattern
    at apply time (passed to extract via --param only for verification, NOT
    written to the cell).

    NO guessing of placeholder position by substring — the pattern is fully
    written by the user. The only thing this function does is check that the
    written pattern, when resolved with the given params, yields exactly the
    literal it was taken from (round‑trip), and fatally fails on any mismatch
    (typical cause: typo in the pattern or wrong parameter).
    """
    if literal_net not in net_template_map:
        return literal_net
    pattern = net_template_map[literal_net]
    check = resolve_net(pattern, params, {})
    if check != literal_net:
        raise ValidationError(format_fatal_error(
            _("--net-template for {literal!r} fails round‑trip check").format(literal=literal_net),
            [_("pattern {pattern!r} with params={params} resolves to {check!r}, "
               "not to {literal!r} — typo in pattern or wrong parameter passed via --param")
             .format(pattern=pattern, params=params, check=check, literal=literal_net)]
        ))
    return pattern