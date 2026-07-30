# kicadspoke/validation.py
"""
validation.py — fatal pre‑validation checks, executed BEFORE planning and any
board modifications. If a problem is found, a ValidationError is raised with a
clear, consolidated message listing all issues at once (not one error per run).

CHANGED (KiCadSpoke 4.0): previously explicit refs in config
(component1_ref/component2_ref) were checked — they no longer exist; components
are selected from ComponentPool by (real net, role). The main protection is now
built into ComponentPool.pop() itself (fatal on shortage), but here we do the
same accounting IN ADVANCE to see all shortages at once, rather than stopping
at the first spoke.
"""
import logging
import difflib
from typing import List, Dict, Optional

from .config import Config
from .kicad.adapter import KiCadBoardAdapter
from .exceptions import ValidationError, format_fatal_error
from .net_resolution import resolve_net
from .placement.services.component_pool import ComponentPool
from .placement.services.clone_role_resolver import (
    clone_uses_selection_mode,
    resolve_footprint_by_role,
)
from .i18n import _

logger = logging.getLogger(__name__)


def check_templates_and_pads_exist(adapter: KiCadBoardAdapter, cfg: Config, sheet_names=None) -> None:
    """
    Every spoke must reference an existing template and an existing pad of the
    target component — otherwise the spoke is simply skipped silently (which
    would make it easy to miss a typo in the template name/pad number).
    """
    problems = []
    anchors = {}
    for rule in cfg.rules:
        # Resolve anchor: either anchor_ref or anchor_role
        if rule.anchor_ref is not None:
            fp = adapter.get_footprint(rule.anchor_ref)
            if fp is None:
                problems.append(_("rule (net {net!r}): anchor {anchor!r} not found on board")
                                .format(net=rule.net, anchor=rule.anchor_ref))
            anchors[rule.anchor_ref] = fp
        else:
            try:
                fp = resolve_footprint_by_role(
                    adapter,
                    rule.anchor_role,
                    rule.anchor_sheet,
                    rule.anchor_cluster,
                    sheet_names or {},
                    label=_("rule (net {net!r})").format(net=rule.net)
                )
                anchors[f"role:{rule.anchor_role}"] = fp
            except ValidationError as e:
                problems.append(str(e))

    for rule in cfg.rules:
        if rule.anchor_ref is not None:
            target_fp = anchors.get(rule.anchor_ref)
        else:
            target_fp = anchors.get(f"role:{rule.anchor_role}")
        if target_fp is None:
            continue
        for spoke in rule.spokes:
            if not spoke.enabled:
                continue
            if spoke.template not in cfg.templates:
                problems.append(_("spoke (pad {pad}, net {net!r}): template {template!r} not found in templates")
                                .format(pad=spoke.pad, net=rule.net, template=spoke.template))
                continue
            pad = adapter.get_pad_by_number(target_fp, spoke.pad) if target_fp else None
            if target_fp is not None and pad is None:
                anchor_name = rule.anchor_ref if rule.anchor_ref is not None else rule.anchor_role
                problems.append(_("spoke (template {template!r}, net {net!r}): {anchor!r} has no pad {pad!r}")
                                .format(template=spoke.template, net=rule.net,
                                        anchor=anchor_name, pad=spoke.pad))

    if problems:
        raise ValidationError(format_fatal_error(
            _("spoke references a non‑existent template or pad"),
            problems
        ))
    logger.debug(_("Template/pad checks for spokes: all references valid"))


def check_role_pool_sufficiency(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    For each rule net, pre‑counts how many components of each role are required
    by all its spokes for each cluster, and checks against the actual number of
    components on the board (same net + Role field + Cluster field) — fatal with
    a list of all shortages at once.
    """
    problems = []

    for rule in cfg.rules:
        # Collect all roles needed for this rule
        roles_needed = set()
        for spoke in rule.spokes:
            if not spoke.enabled:
                continue
            template = cfg.templates.get(spoke.template)
            if template is None:
                continue
            for slot in template.components:
                roles_needed.add(slot.role)

        if not roles_needed:
            continue

        # Collect all clusters used in spokes (including None)
        clusters_needed = set()
        for spoke in rule.spokes:
            if not spoke.enabled:
                continue
            clusters_needed.add(spoke.cluster)  # None is allowed

        # Initialise requirement dictionary per cluster
        needed_by_cluster: Dict[Optional[str], Dict[str, int]] = {
            cluster: {role: 0 for role in roles_needed}
            for cluster in clusters_needed
        }

        # Fill requirements
        for spoke in rule.spokes:
            if not spoke.enabled:
                continue
            template = cfg.templates.get(spoke.template)
            if template is None:
                continue
            cluster = spoke.cluster
            for slot in template.components:
                needed_by_cluster[cluster][slot.role] += 1

        # For each cluster, check sufficiency
        for cluster, needed_counts in needed_by_cluster.items():
            if not any(needed_counts.values()):
                continue

            pool = ComponentPool(adapter, rule.net, roles=sorted(roles_needed), cluster=cluster)
            for role, needed in needed_counts.items():
                if needed == 0:
                    continue
                available = pool.remaining_count(role)
                if available < needed:
                    cluster_label = _(" (cluster {cluster!r})").format(cluster=cluster) if cluster is not None else ""
                    problems.append(
                        _("net {net!r}, role {role!r}{cluster}: need {needed}, found {available} "
                          "(check the Role and Cluster fields in the schematic and the actual net connection)")
                        .format(net=rule.net, role=role, cluster=cluster_label,
                                needed=needed, available=available)
                    )

    if problems:
        raise ValidationError(format_fatal_error(
            _("not enough components for template roles"),
            problems
        ))
    logger.debug(_("Role pool sufficiency checks passed"))


def check_clone_templates_exist(cfg: Config) -> None:
    """
    Every ClonePlacement with template (not role) must reference an existing
    template — pure config check, does not require the live board. role‑based
    placements are skipped: their template is intentionally None, and
    ClonePositionCalculator synthesises a single‑component template on the fly,
    so there is nothing to check in cfg.templates.
    """
    problems = []
    for clone in cfg.clone_placements:
        if not clone.enabled or clone.template is None:
            continue
        if clone.template not in cfg.templates:
            problems.append(_("clone_placement {name!r}: template {template!r} not found in templates")
                            .format(name=clone.name, template=clone.template))
    if problems:
        raise ValidationError(format_fatal_error(
            _("clone_placement references a non‑existent template"),
            problems
        ))
    logger.debug(_("Clone template existence checks passed"))


def check_no_duplicate_clone_anchors(cfg: Config) -> None:
    """
    Pure config check (does not require live board):
      1. clone_placements[].name must be unique — this is the primary identifier
         for anchor‑less placements (see clone_anchor_id) and good hygiene.
      2. (content, anchor_ref, anchor_pad, origin_x_mm, origin_y_mm) among
         clone_placements with anchor_ref set must be unique — this mirrors
         the identity used by the registry (registry.py, see clone_anchor_id).
         If two different clone_placements accidentally point to the same
         physical anchor AND the same offset, the registry will confuse their
         vias/tracks. This is almost certainly a copy‑paste typo (forgot to
         change anchor_pad or origin_x_mm/origin_y_mm in the second block),
         not intentional. "Content" is template OR role — two different roles
         on the same anchor are NOT duplicates (different components at the
         same point is normal), so we use what is actually set, not
         clone.template (which is None for role‑based placements, and two
         different roles would collapse into one key if we only used
         template). origin_x_mm/origin_y_mm is included (found 2026-07-27)
         because it's legitimate for two clones to share an anchor and differ
         only by this offset (e.g. a positive/negative filter pair mirrored
         off the same connector pad) — without it in the key, that legitimate
         case was indistinguishable from a real duplicate, both to this check
         and to the registry itself. anchor_cluster is included in the
         anchor_role key (found 2026-07-28, same reasoning): p5v_led_spoke/
         n5v_led_spoke share identical anchor_role/anchor_sheet/anchor_pad/
         origin and differ ONLY by anchor_cluster (Pos vs Neg, the field that
         actually picks which physical component the anchor resolves to) —
         without it here, this check false-positived on that legitimate pair.
    """
    problems = []
    seen_names = {}
    seen_ref_anchors = {}
    seen_role_anchors = {}
    for clone in cfg.clone_placements:
        if not clone.enabled:
            continue
        if clone.name in seen_names:
            problems.append(_("name {name!r} appears twice in clone_placements — names must be unique")
                            .format(name=clone.name))
        seen_names[clone.name] = True

        content_id = clone.template if clone.template is not None else _("role:{role}").format(role=clone.role)
        origin = (round(clone.origin_x_mm, 4), round(clone.origin_y_mm, 4))

        if clone.anchor_ref is not None:
            key = (content_id, clone.anchor_ref, clone.anchor_pad, origin)
            if key in seen_ref_anchors:
                problems.append(
                    _("{this!r} and {other!r} both point to the same anchor with the same offset "
                      "(template/role={content!r}, anchor_ref={ref!r}, anchor_pad={pad!r}, "
                      "origin=({ox}, {oy}) mm) — the registry would confuse their vias/tracks; "
                      "likely a copy‑paste typo (if this is intentional, give them different "
                      "origin_x_mm/origin_y_mm)")
                    .format(this=clone.name, other=seen_ref_anchors[key], content=content_id,
                            ref=clone.anchor_ref, pad=clone.anchor_pad, ox=origin[0], oy=origin[1])
                )
            seen_ref_anchors[key] = clone.name

        if clone.anchor_role is not None:
            key = (content_id, clone.anchor_role, clone.anchor_sheet, clone.anchor_cluster,
                   clone.anchor_pad, origin)
            if key in seen_role_anchors:
                problems.append(
                    _("{this!r} and {other!r} both point to the same anchor with the same offset "
                      "(template/role={content!r}, anchor_role={role!r}, anchor_sheet={sheet!r}, "
                      "anchor_cluster={cluster!r}, anchor_pad={pad!r}, origin=({ox}, {oy}) mm) — "
                      "the registry would confuse their vias/tracks; likely a copy‑paste typo (if "
                      "this is intentional, give them different origin_x_mm/origin_y_mm)")
                    .format(this=clone.name, other=seen_role_anchors[key], content=content_id,
                            role=clone.anchor_role, sheet=clone.anchor_sheet, cluster=clone.anchor_cluster,
                            pad=clone.anchor_pad, ox=origin[0], oy=origin[1])
                )
            seen_role_anchors[key] = clone.name

    if problems:
        raise ValidationError(format_fatal_error(
            _("clone_placements with ambiguous identity"),
            problems
        ))
    logger.debug(_("Duplicate clone anchor checks passed"))


def check_anchor_sheet_configured(cfg: Config, sheet_names=None) -> None:
    """
    Pure config check. anchor_sheet is resolved via sheet_names.
    If sheet_names is empty, it means neither schematic_dir nor schematic_files
    were set (or none of the .kicad_sch files could be parsed), and anchor_sheet
    will NEVER narrow anything — it will silently do nothing, and later ambiguity
    of anchor_role will fail with a less helpful fatal. Better to say it upfront.
    """
    _sn = sheet_names or {}
    users = [c.name for c in cfg.clone_placements if c.enabled and c.anchor_sheet]
    if users and not _sn:
        raise ValidationError(format_fatal_error(
            _("anchor_sheet is used but sheet name dictionary is empty"),
            [_("clone_placements with anchor_sheet: {users}").format(users=users),
             _("you need schematic_dir (or schematic_files) at the root of the config — "
               "path to the folder with *.kicad_sch files, relative to this YAML")]
        ))
    logger.debug(_("anchor_sheet/sheet_names check passed"))


def check_clone_nets_exist_on_board(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    Resolves via.net for EACH clone_placement (both spoke‑level and those nested
    in components[i].vias — see apply_clone_geometry) and checks the result
    against the real board nets (adapter.get_all_nets()).

    Why separate from resolve_roles_by_nets: role‑to‑ref mapping already checks
    itself (candidates are searched among real pads, a non‑existent net simply
    yields no candidates — which is fatal). But via.net goes straight into
    ViaCommand without such checking — a typo in net_overrides or params that
    yields a syntactically valid string (e.g. "+3V3_DVD" instead of "+3V3_DVDD")
    would quietly create a via on the wrong net, with no fatal along the way.
    This check is that missing dictionary.

    via.net=None is not checked here — that is already fatal in clone_geometry.py
    (ClonePlacement has no default net), no need to duplicate.
    """
    problems = []
    real_nets = {n.name for n in adapter.get_all_nets()}

    def _check_via(via, clone, where: str):
        if via.net is None:
            return
        try:
            resolved = resolve_net(via.net, clone.params, clone.net_overrides)
        except ValidationError:
            return  # missing parameter — already a fatal error higher up
        if resolved not in real_nets:
            hint = difflib.get_close_matches(resolved, real_nets, n=1)
            suggestion = _(" — did you mean {suggestion!r}?").format(suggestion=hint[0]) if hint else ""
            problems.append(
                _("{name!r}, {where}: via.net {template!r} resolves to {resolved!r}, "
                  "but that net does not exist on the board{suggestion}")
                .format(name=clone.name, where=where, template=via.net,
                        resolved=resolved, suggestion=suggestion)
            )

    for clone in cfg.clone_placements:
        if not clone.enabled:
            continue
        template = cfg.templates.get(clone.template)
        if template is None:
            continue  # already caught by check_clone_templates_exist
        for via in template.vias:
            _check_via(via, clone, _("spoke‑level via"))
        for slot in template.components:
            for via in slot.vias:
                _check_via(via, clone, _("via of role {role!r}").format(role=slot.role))

    if problems:
        raise ValidationError(format_fatal_error(
            _("resolved via net references a non‑existent board net"),
            problems
        ))
    logger.debug(_("clone via.net checks against real board nets passed"))


def check_single_selection_based_clone(cfg: Config) -> None:
    """
    In KiCad only ONE selection is active at any moment — therefore you cannot
    process more than one ClonePlacement in "by selection" mode (no nets, no params)
    in a single run. If more than one, fatal with a hint to either disable the
    extras (enabled: false) or run apply separately for each with --only NAME.
    """
    selection_based = [c.name for c in cfg.clone_placements if c.enabled and clone_uses_selection_mode(c)]
    if len(selection_based) > 1:
        raise ValidationError(format_fatal_error(
            _("multiple clone_placements in 'by selection' mode in one run"),
            [_("found {count}: {names} — KiCad has only one selection at a time, "
               "so processing all at once is impossible").format(
                   count=len(selection_based), names=selection_based),
             _("solution: either set enabled: false on all but one, or run apply "
               "separately for each using --only NAME")]
        ))
    logger.debug(_("Single selection‑based clone check passed"))


def run_all_checks(adapter: KiCadBoardAdapter, cfg: Config, sheet_names=None) -> None:
    """Runs all checks in order — from cheap to more comprehensive."""
    _sn = sheet_names or {}
    logger.info(_("Running pre‑validation checks..."))
    check_clone_templates_exist(cfg)
    check_no_duplicate_clone_anchors(cfg)
    check_anchor_sheet_configured(cfg, sheet_names=_sn)
    check_single_selection_based_clone(cfg)
    check_templates_and_pads_exist(adapter, cfg, sheet_names=_sn)
    check_role_pool_sufficiency(adapter, cfg)
    check_clone_nets_exist_on_board(adapter, cfg)
    logger.info(_("All pre‑validation checks passed"))