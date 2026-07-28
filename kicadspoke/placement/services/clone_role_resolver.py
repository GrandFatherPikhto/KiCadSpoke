# kicadspoke/placement/services/clone_role_resolver.py
"""
clone_role_resolver.py — role‑to‑ref mapping for ClonePlacement, two independent mechanisms:

  1. By selection (resolve_roles_by_selection) — for rare, one‑off sections
     (e.g. a single MCU on the board). The user selects the components of a
     specific, not‑yet‑placed instance with the mouse. Symmetric check: every
     role in the template must be found in the selection exactly once, and
     conversely, no role in the selection may be absent from the template.

  2. By nets (resolve_roles_by_nets) — for repeated templates (PI‑filters, DAC
     channels) where selection risks mixing up identical‑looking instances.
     The net for each role is: priority — explicit ClonePlacement.nets[role]
     (literal), otherwise TemplateComponentSlot.net_template (with placeholders,
     via net_resolution.resolve_net). No geometry‑based or ref‑pattern matching
     — only explicitly specified nets.

The mode is chosen BEFORE calling this module (see planner/orchestration):
if ClonePlacement has nets or params set — mode is "by nets", otherwise "by
selection". This is final, no automatic mode switching inside the resolver.
"""
import logging
import math
from typing import Dict, List, Optional
from kipy.board_types import FootprintInstance
from kipy.geometry import Vector2

from ...config import SpokeTemplate, ClonePlacement
from ...exceptions import ValidationError, format_fatal_error
from ...net_resolution import resolve_net, resolve_placeholder
from ...utils.units import MM
from .component_pool import ROLE_FIELD_NAME, _cluster_prefix_match
from ...constants import CLUSTER_FIELD_NAME
from ...i18n import _

logger = logging.getLogger(__name__)


def clone_uses_selection_mode(clone: ClonePlacement) -> bool:
    """
    Returns True if the clone is in "by selection" mode:
      - by_selection: true is explicitly set (priority — see ClonePlacement.
        by_selection is needed separately from implicit inference because params
        is ALSO used for resolving placeholders in via/track nets — without this
        flag, a params intended only for via resolution would silently switch
        the whole clone_placement to "by nets" mode, breaking roles resolved by
        selection), OR
      - neither nets nor params are set (old implicit behaviour, default for
        backward compatibility).
    This is the single place where the decision is made — both
    ClonePositionCalculator and validation.py must ask here, not duplicate the rule.
    """
    if clone.by_selection:
        return True
    return not (clone.nets or clone.params)


def _narrow_ambiguous_candidates(candidates, clone: ClonePlacement, adapter, selected_refs: set,
                                 anchor_position: Optional[Vector2], clone_name: str, role: str):
    """
    Common narrowing cascade for ambiguous candidates: Cluster -> current
    selection -> physical proximity to anchor. Used both in resolve_roles_by_nets
    (after net‑based matching) and in resolve_roles_by_selection (when a role is
    not found in the selection but is ambiguous on the whole board) — one
    narrowing logic, not two copies. Returns (narrowed list, note for error
    message — empty string if narrowed to one).
    """
    narrowed = list(candidates)

    if clone.anchor_cluster:
        by_cluster = [fp for fp in narrowed
                     if _cluster_prefix_match(
                         adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or '',
                         clone.anchor_cluster)]
        if by_cluster and len(by_cluster) < len(narrowed):
            logger.info(_("[{name}] role {role!r}: {count} candidates narrowed to {narrowed} by anchor_cluster {cluster!r}")
                        .format(name=clone_name, role=role, count=len(narrowed),
                                narrowed=len(by_cluster), cluster=clone.anchor_cluster))
            narrowed = by_cluster

    if len(narrowed) > 1 and selected_refs:
        by_selection = [fp for fp in narrowed if fp.reference_field.text.value in selected_refs]
        if by_selection and len(by_selection) < len(narrowed):
            logger.info(_("[{name}] role {role!r}: {count} candidates narrowed to {narrowed} by current selection")
                        .format(name=clone_name, role=role, count=len(narrowed), narrowed=len(by_selection)))
            narrowed = by_selection

    note = ""
    if len(narrowed) > 1 and anchor_position is not None:
        with_dist = sorted(
            ((math.hypot((fp.position.x - anchor_position.x) / MM,
                         (fp.position.y - anchor_position.y) / MM), fp)
             for fp in narrowed),
            key=lambda t: t[0]
        )
        closest_dist, closest_fp = with_dist[0]
        second_dist = with_dist[1][0]
        note = _(" (closest to anchor {name!r}: {ref} at {d:.2f} mm, second — {d2:.2f} mm)")
        note = note.format(name=clone_name, ref=closest_fp.reference_field.text.value,
                           d=closest_dist, d2=second_dist)
        if second_dist >= 2 * max(closest_dist, 1e-6):
            logger.info(_("[{name}] role {role!r}: {count} candidates narrowed to 1 by physical proximity to anchor "
                          "({ref}, {d:.2f} mm, second closest — {d2:.2f} mm, sufficient gap)")
                        .format(name=clone_name, role=role, count=len(narrowed),
                                ref=closest_fp.reference_field.text.value,
                                d=closest_dist, d2=second_dist))
            narrowed = [closest_fp]
        else:
            logger.debug(_("[{name}] role {role!r}: cannot narrow by proximity — "
                           "{d:.2f} mm vs {d2:.2f} mm, insufficient gap")
                         .format(name=clone_name, role=role, d=closest_dist, d2=second_dist))

    return narrowed, note


def resolve_roles_by_selection(adapter, template: SpokeTemplate, clone: ClonePlacement,
                               anchor_position: Optional[Vector2] = None) -> Dict[str, str]:
    """
    Mapping by current selection — but selection is MANDATORY ONLY when the role
    is truly ambiguous:
      1. role is in selection -> use it (priority over everything below).
      2. role is NOT in selection, but it is unique on the WHOLE board -> resolve
         directly, no selection needed.
      3. role is NOT in selection and is ambiguous on the board -> same narrowing
         cascade as in resolve_roles_by_nets: Cluster -> selection (again, in
         case the selection contains some of these candidates without the role
         itself... rare but harmless) -> physical proximity to anchor -> FATAL
         with the exact list if still ambiguous.
    """
    items = adapter.get_selected_items()
    footprints = [i for i in items if isinstance(i, FootprintInstance)]
    selected_refs = {fp.reference_field.text.value for fp in footprints}
    clone_name = clone.name

    template_roles = {slot.role for slot in template.components}

    role_to_ref: Dict[str, str] = {}
    problems: List[str] = []

    for fp in footprints:
        ref = fp.reference_field.text.value
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is None:
            problems.append(_("{ref}: no {field!r} field").format(ref=ref, field=ROLE_FIELD_NAME))
            continue
        if role not in template_roles:
            problems.append(_("{ref}: role {role!r} is not in the template "
                              "(template roles: {roles})")
                            .format(ref=ref, role=role, roles=sorted(template_roles)))
            continue
        if role in role_to_ref:
            problems.append(_("role {role!r} appears twice in selection: {ref1!r} and {ref2!r}")
                            .format(role=role, ref1=role_to_ref[role], ref2=ref))
            continue
        role_to_ref[role] = ref

    missing = template_roles - set(role_to_ref.keys())
    if missing:
        all_fps_by_role: Dict[str, list] = {}
        for fp in adapter.get_footprints():
            role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
            if role in missing:
                all_fps_by_role.setdefault(role, []).append(fp)

        for role in sorted(missing):
            candidates = all_fps_by_role.get(role, [])
            if not candidates:
                problems.append(_("role {role!r} is in template but not found anywhere on board")
                                .format(role=role))
                continue
            if len(candidates) == 1:
                ref = candidates[0].reference_field.text.value
                role_to_ref[role] = ref
                logger.info(_("[{name}] role {role!r} -> {ref} (unique on whole board, no selection needed)")
                            .format(name=clone_name, role=role, ref=ref))
                continue

            narrowed, note = _narrow_ambiguous_candidates(
                candidates, clone, adapter, selected_refs, anchor_position, clone_name, role
            )
            if len(narrowed) == 1:
                role_to_ref[role] = narrowed[0].reference_field.text.value
            else:
                refs = sorted(fp.reference_field.text.value for fp in narrowed)
                problems.append(_("role {role!r} is in template, not found in selection, and ambiguous on board "
                                  "({count} candidates: {refs}){note} — set anchor_cluster, OR select the "
                                  "desired instance on the board before running")
                                .format(role=role, count=len(narrowed), refs=refs, note=note))

    if problems:
        raise ValidationError(format_fatal_error(
            _("selection does not match template composition ({name!r})").format(name=clone_name),
            problems
        ))

    logger.info(_("[{name}] mapped by selection: {count} roles").format(name=clone_name, count=len(role_to_ref)))
    return role_to_ref


def resolve_roles_by_nets(adapter, template: SpokeTemplate, clone: ClonePlacement,
                          anchor_position: Optional[Vector2] = None) -> Dict[str, str]:
    """
    Mapping by explicit/parameterised nets (without mouse selection as the
    PRIMARY mechanism — but current selection, if any, participates as a
    narrowing step, see below).

    Ambiguity resolution cascade (each step only NARROWS, never chooses for the user):
      0. clone.refs[role] — explicit override, bypassing search entirely. Breaks
         on re‑annotation (refdes is not stable) — last resort, not the main path.
      1. candidates = Role field matches AND sits on the expected net.
      2. if several candidates AND clone.anchor_cluster is set — narrow to
         candidates whose Cluster field matches by prefix segments
         (see _cluster_prefix_match). This is the main path for the typical case
         "N identical roles on one sheet because the net is common power, not
         per‑channel" — previously we attempted to narrow by sheet_path (see
         history: sheet_path.path UUID is unique per‑component, gives no sheet
         grouping at all — that step was a silent no‑op).
      3. still several — narrow to intersection with the CURRENT selection on
         the board, if non‑empty and narrows something.
      4. still several and anchor_position is set — narrow by physical proximity
         to the anchor of THIS clone_placement: the closest candidate wins, but
         only with a clear gap (closest is at least twice as close as the second)
         — otherwise it's a coin toss, fatal. Independent of refdes/sheet/net —
         survives re‑annotation.
      5. still several — FATAL: candidates are indistinguishable by all
         available means. Suggest either splitting roles by names in the
         schematic, setting anchor_cluster, selecting the desired instance, or
         (last resort) explicit refs.
    """
    selected_items = adapter.get_selected_items()
    selected_refs = {i.reference_field.text.value for i in selected_items
                     if isinstance(i, FootprintInstance)}

    all_fps = adapter.get_footprints()
    fps_by_role: Dict[str, list] = {}
    fps_by_ref = {}
    for fp in all_fps:
        fps_by_ref[fp.reference_field.text.value] = fp
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is not None:
            fps_by_role.setdefault(role, []).append(fp)

    role_to_ref: Dict[str, str] = {}
    problems: List[str] = []
    ambiguous: List = []   # (role, expected_net, matched) for second pass

    # --- step 0: explicit refs ---
    for role, ref in clone.refs.items():
        if role not in {s.role for s in template.components}:
            problems.append(_("refs: role {role!r} does not exist in template {template!r}")
                            .format(role=role, template=template.name))
            continue
        fp = fps_by_ref.get(ref)
        if fp is None:
            problems.append(_("refs: component {ref!r} (role {role!r}) not found on board")
                            .format(ref=ref, role=role))
            continue
        role_to_ref[role] = ref
        logger.info(_("[{name}] role {role!r} -> {ref} (explicit refs)")
                    .format(name=clone.name, role=role, ref=ref))

    # --- first pass: unambiguous by Role+net ---
    for slot in template.components:
        role = slot.role
        if role in role_to_ref:
            continue

        if role in clone.nets:
            net_template = clone.nets[role]
        elif slot.net_template is not None:
            net_template = slot.net_template
        else:
            problems.append(_("role {role!r}: no net for mapping (neither in nets "
                              "of {name!r}, nor in template net_template) — in 'by nets' "
                              "mode, a net is required for every role")
                            .format(role=role, name=clone.name))
            continue

        expected_net = resolve_net(net_template, clone.params, clone.net_overrides)

        candidates = fps_by_role.get(role, [])
        matched = []
        for fp in candidates:
            pads = adapter.get_footprint_pads(fp)
            nets_on_fp = {p.net.name for p in pads if p.net and p.net.name}
            if expected_net in nets_on_fp:
                matched.append(fp)

        if not candidates:
            problems.append(_("role {role!r}: NO component with this role on the board at all "
                              "(check the Role field in the schematic, and that Update PCB from Schematic was run)")
                            .format(role=role))
        elif not matched:
            found_nets = sorted({n for fp in candidates for n in
                                 {p.net.name for p in adapter.get_footprint_pads(fp) if p.net and p.net.name}})
            refs = sorted(fp.reference_field.text.value for fp in candidates)
            problems.append(_("role {role!r}: component(s) {refs} with this role exist on the board, "
                              "but none is on net {expected!r} — they are actually on {found} "
                              "(check params/net name or the schematic connection)")
                            .format(role=role, refs=refs, expected=expected_net, found=found_nets))
        elif len(matched) > 1:
            ambiguous.append((role, expected_net, matched))
        else:
            role_to_ref[role] = matched[0].reference_field.text.value

    # --- narrowing ambiguous: Cluster -> selection -> physical proximity (common function) ---
    for role, expected_net, matched in ambiguous:
        narrowed, note = _narrow_ambiguous_candidates(
            matched, clone, adapter, selected_refs, anchor_position, clone.name, role
        )

        if len(narrowed) == 1:
            role_to_ref[role] = narrowed[0].reference_field.text.value
        else:
            refs = sorted(fp.reference_field.text.value for fp in narrowed)
            cluster_hint = (_(" (already narrowed by anchor_cluster {cluster!r}, but not enough)")
                            .format(cluster=clone.anchor_cluster) if clone.anchor_cluster
                            else _(" (Cluster not set — if these components are physically different "
                                   "instances, anchor_cluster would narrow to one)"))
            problems.append(
                _("role {role!r}: ambiguity — {count} components on net {net!r}{cluster_hint}{note}: {refs}. "
                  "Solutions: set anchor_cluster (if Cluster is assigned in the schematic), OR select the "
                  "desired instance on the board before running, OR split roles by net names in the schematic "
                  "(e.g. DAC_PI_3V3_C1 vs DAC_PI_AVDD_C1), OR use explicit refs: {{ {role}: {first_ref} }}")
                .format(role=role, count=len(narrowed), net=expected_net,
                        cluster_hint=cluster_hint, note=note, refs=refs,
                        first_ref=refs[0])
            )

    if problems:
        raise ValidationError(format_fatal_error(
            _("net‑based mapping failed ({name!r})").format(name=clone.name),
            problems
        ))

    logger.info(_("[{name}] mapped by nets: {count} roles").format(name=clone.name, count=len(role_to_ref)))
    return role_to_ref


def _fp_on_sheet(fp, anchor_sheet: str, sheet_names: Dict[str, str]) -> bool:
    """
    anchor_sheet appears as ONE OF THE SEGMENTS of the human‑readable path of fp
    (not necessarily the last one — the component may be deeper than the specified
    sheet). The path is built via sheet_names (see kicadspoke/sheet_names.py) —
    direct parsing of .kicad_sch, empirically confirmed on a real project
    (0 conflicts, 0 unresolved UUIDs on mishin‑coil). Works for ANY component —
    unlike the previous approach via local net names, does not require fp to have
    a local net at all.
    """
    from ...sheet_names import resolve_sheet_path_names
    names = resolve_sheet_path_names(fp, sheet_names)
    return anchor_sheet in names


def resolve_footprint_by_role(adapter, anchor_role: str, anchor_sheet: Optional[str],
                              anchor_cluster: Optional[str], sheet_names: Dict[str, str],
                              label: str) -> FootprintInstance:
    """
    Resolves ANY anchor component by anchor_role (Role field on the board,
    NOT a template role — this is different: here we search for the anchor itself
    among ALL footprints on the board, not roles inside the cloned template).
    Not tied to ClonePlacement — used both by it (resolve_anchor_by_role below,
    thin wrapper) and by Rule (see manual_position_calculator.py) for anchor_role
    in spoke paths. The same ambiguity narrowing cascade:

      1. candidates = all footprints with Role == anchor_role.
      2. several — narrow by anchor_sheet (if set): the human‑readable path of fp
         (via sheet_names, see kicadspoke/sheet_names.py) contains this segment
         (see _fp_on_sheet).
      2b. still several — narrow by anchor_cluster (if set):
          Cluster field matches by prefix segments (see _cluster_prefix_match) —
          independent of anchor_sheet, read from the schematic, not from UUID/sheet_path.
      3. still several — narrow to the current selection on the board.
      4. still several, or 0 — FATAL with candidate list and hints
         (anchor_sheet/anchor_cluster/selection/explicit anchor_ref).

    label — only for error messages (clone.name for ClonePlacement,
    rule.net for Rule — Rule has no "name", net serves as label).
    sheet_names — {uuid: Sheetname}, see Config.sheet_names; empty dictionary
    (schematic_dir/schematic_files not set) — anchor_sheet then never narrows
    anything (fatal checked earlier in validation.py).
    """
    all_fps = adapter.get_footprints()
    candidates = [fp for fp in all_fps
                  if adapter.get_field_value(fp, ROLE_FIELD_NAME) == anchor_role]

    if not candidates:
        raise ValidationError(format_fatal_error(
            _("{label}: anchor_role {role!r} not found on any component on the board")
            .format(label=label, role=anchor_role),
            [_("check that the Role field is set in the schematic and propagated to the PCB "
               "(Update PCB from Schematic)")]
        ))

    narrowed = candidates
    if len(narrowed) > 1 and anchor_sheet:
        by_sheet = [fp for fp in narrowed if _fp_on_sheet(fp, anchor_sheet, sheet_names)]
        if by_sheet:
            if len(by_sheet) < len(narrowed):
                logger.info(_("[{label}] anchor_role {role!r}: {count} candidates narrowed to {narrowed} "
                              "by anchor_sheet {sheet!r}")
                            .format(label=label, role=anchor_role, count=len(narrowed),
                                    narrowed=len(by_sheet), sheet=anchor_sheet))
            narrowed = by_sheet

    if len(narrowed) > 1 and anchor_cluster:
        by_cluster = [fp for fp in narrowed
                     if _cluster_prefix_match(
                         adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or '',
                         anchor_cluster)]
        if by_cluster:
            if len(by_cluster) < len(narrowed):
                logger.info(_("[{label}] anchor_role {role!r}: {count} candidates narrowed to {narrowed} "
                              "by anchor_cluster {cluster!r}")
                            .format(label=label, role=anchor_role, count=len(narrowed),
                                    narrowed=len(by_cluster), cluster=anchor_cluster))
            narrowed = by_cluster

    if len(narrowed) > 1:
        selected_items = adapter.get_selected_items()
        selected_refs = {i.reference_field.text.value for i in selected_items
                         if isinstance(i, FootprintInstance)}
        if selected_refs:
            by_selection = [fp for fp in narrowed
                            if fp.reference_field.text.value in selected_refs]
            if by_selection and len(by_selection) < len(narrowed):
                logger.info(_("[{label}] anchor_role {role!r}: {count} candidates narrowed to {narrowed} "
                              "by current selection")
                            .format(label=label, role=anchor_role, count=len(narrowed),
                                    narrowed=len(by_selection)))
                narrowed = by_selection

    if len(narrowed) == 1:
        return narrowed[0]

    refs = sorted(fp.reference_field.text.value for fp in narrowed)
    raise ValidationError(format_fatal_error(
        _("{label}: anchor_role {role!r} is ambiguous").format(label=label, role=anchor_role),
        [_("candidates: {count} — {refs}. Solutions: refine anchor_sheet "
           "and/or anchor_cluster, OR select the desired instance on the board "
           "before running, OR use explicit anchor_ref instead of anchor_role: {first_ref!r}")
         .format(count=len(narrowed), refs=refs, first_ref=refs[0])]
    ))


def resolve_anchor_by_role(adapter, clone: ClonePlacement, sheet_names: Dict[str, str]) -> FootprintInstance:
    """Thin wrapper of resolve_footprint_by_role for ClonePlacement — backward
    compatibility for calling code (clone_position_calculator.py).

    anchor_sheet supports {placeholder} substitution from clone.params (same
    mechanism as nets/net_template, via resolve_placeholder) — unlike Rule
    (manual_position_calculator.py), which has no params field and always
    uses anchor_sheet literally."""
    anchor_sheet = clone.anchor_sheet
    if anchor_sheet is not None:
        anchor_sheet = resolve_placeholder(anchor_sheet, clone.params, what="anchor_sheet")
    return resolve_footprint_by_role(adapter, clone.anchor_role, anchor_sheet,
                                     clone.anchor_cluster, sheet_names, clone.name)