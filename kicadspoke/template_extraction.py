# kicadspoke/template_extraction.py
"""
template_extraction.py — extracts a spoke template from the current selection
on the board (not from sheet_path/schematic hierarchy — we decided that
selection is more reliable and independent of hierarchical sheets).

Algorithm:
  1. Selection (expanding Groups, see adapter.get_selected_items()) is split
     into footprints, vias, and tracks; everything else is ignored.
  2. origin = lower‑left corner of the selection bounding box
     (min_x, max_y) — in KiCad's native coordinates this is visually the
     lower‑left corner because Y grows downward.
  3. Each footprint: along/across = its current position MINUS origin,
     angle as‑is (the current selection state is the "reference at rotation_deg=0",
     no separate recalculation needed).
  4. Each via: same formula, but WITHOUT a role — vias have no user fields,
     so it is impossible to automatically determine "which" component it belongs
     to; all extracted vias always go into the spoke‑level vias list (not inside
     a specific component slot). The user can manually move vias into
     components[i].vias in the resulting YAML if needed.
  5. Each SELECTED track is included ONLY IF BOTH its ends match (within
     POSITION_TOLERANCE_MM) something else in the selection — a pad of a
     selected component, a selected via, or the end of another selected track
     (for butt‑joints without a via). A track whose end goes "nowhere" (e.g.,
     a long track to +3V3 sticking out of the intended area) is skipped with
     a warning, rather than included as‑is: KiCad can select such a track
     entirely, even if it physically extends far beyond what we actually wanted
     to copy.

Roles (Role field) MUST be unique within the selection — fatal error at
extraction time, not only during later template loading.
"""
import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from kipy.board_types import FootprintInstance, Via, Track
from kipy.geometry import Vector2

from .constants import POSITION_TOLERANCE_MM, ROLE_FIELD_NAME
from .exceptions import ValidationError, format_fatal_error
from .kicad.adapter import KiCadBoardAdapter
from .net_resolution import parametrize_net
from .utils.units import MM
from .i18n import _

logger = logging.getLogger(__name__)



def _points_match(p1: Vector2, p2: Vector2, tol_mm: float = POSITION_TOLERANCE_MM) -> bool:
    return abs(p1.x - p2.x) / MM <= tol_mm and abs(p1.y - p2.y) / MM <= tol_mm


def _point_matches_any(point: Vector2, anchors: List[Vector2]) -> bool:
    return any(_points_match(point, a) for a in anchors)


_BBOX_EPSILON_MM = 0.001  # NOT a routing tolerance (the real bbox of via/pad
                          # already provides all the needed margin — via radius
                          # is usually an order of magnitude larger than any
                          # manual routing error). This is purely a defence
                          # against coordinate quantisation/float rounding when
                          # converting to nm, not a "how crookedly the track is
                          # attached" tolerance.


def _inflated_boxes(adapter: KiCadBoardAdapter, items: List[Any]) -> List[Any]:
    boxes = adapter.get_bounding_boxes(items)
    for b in boxes:
        if b is not None:
            b.inflate(int(_BBOX_EPSILON_MM * MM))
    return boxes


def _point_in_box(point: Vector2, box) -> bool:
    if box is None:
        return False
    return (box.pos.x <= point.x <= box.pos.x + box.size.x
            and box.pos.y <= point.y <= box.pos.y + box.size.y)


def _filter_tracks_within_selection(
    tracks: List[Track], footprints: List[FootprintInstance], vias: List[Via],
    adapter: KiCadBoardAdapter,
) -> List[Track]:
    """
    Keeps only tracks whose BOTH ends match something else in the selection.
    "Match" is not exact coordinate equality (KiCad does not require exact
    coincidence for electrical connectivity — connectivity is about copper
    overlap within the real via/pad footprint, not coordinate to the micron;
    manual routing almost never lands exactly at the centre), but rather that
    the track endpoint falls within the REAL bounding box of the corresponding
    via or pad (+ a small technological margin). For track‑to‑track joints
    (no via/pad between them) no margin is needed — there either the endpoint
    meets endpoint, or they are two different tracks.
    """
    all_pads = [p for fp in footprints for p in adapter.get_footprint_pads(fp)]
    pad_boxes = _inflated_boxes(adapter, all_pads)
    via_boxes = _inflated_boxes(adapter, vias)

    def endpoint_ok(point: Vector2, this_track: Track) -> bool:
        if any(_point_in_box(point, box) for box in via_boxes):
            return True
        if any(_point_in_box(point, box) for box in pad_boxes):
            return True
        for other in tracks:
            if other is this_track:
                continue
            if _points_match(point, other.start) or _points_match(point, other.end):
                return True
        return False

    kept = []
    for t in tracks:
        start_ok = endpoint_ok(t.start, t)
        end_ok = endpoint_ok(t.end, t)
        if start_ok and end_ok:
            kept.append(t)
        else:
            missing = (_("both ends") if not start_ok and not end_ok
                      else _("start") if not start_ok else _("end"))
            logger.warning(_("  track ({sx:.3f},{sy:.3f}) -> ({ex:.3f},{ey:.3f}) mm, net={net}: "
                             "{missing} does not match anything else in the selection — "
                             "probably extends beyond the intended area, skipped")
                           .format(sx=t.start.x/MM, sy=t.start.y/MM,
                                   ex=t.end.x/MM, ey=t.end.y/MM,
                                   net=t.net.name if t.net else None,
                                   missing=missing))
    return kept


def _bbox_origin(footprints: List[FootprintInstance], vias: List[Via]) -> Vector2:
    """(min_x, max_y) — lower‑left corner of the selection bounding box."""
    xs = [fp.position.x for fp in footprints] + [v.position.x for v in vias]
    ys = [fp.position.y for fp in footprints] + [v.position.y for v in vias]
    return Vector2.from_xy(min(xs), max(ys))


def _find_origin(footprints: List[FootprintInstance], vias: List[Via],
                 origin_via_net: Optional[str], origin_component_role: Optional[str],
                 origin_component_pad: Optional[str],
                 adapter: KiCadBoardAdapter) -> Vector2:
    """
    Default origin is bbox (see _bbox_origin). If origin_via_net or
    origin_component_role is set, origin is taken from the specific element
    in the selection (its current position on the board) rather than the bbox.
    origin_via_net and origin_component_role are mutually exclusive (checked in
    kicadspoke_cli.py). origin_component_pad is ONLY a refinement of
    origin_component_role (without it it is meaningless — fatal in CLI):
    without it origin is the component centre, with it the position of the
    specific pad (same principle as anchor_pad in ClonePlacement).
    Fatal if the element is not found or (for via_net) ambiguous — no guessing.
    """
    if origin_via_net is not None:
        candidates = [v for v in vias if v.net and v.net.name == origin_via_net]
        if not candidates:
            raise ValidationError(format_fatal_error(
                _("--origin-by-via-net {net!r} not found in selection").format(net=origin_via_net),
                [_("among {count} selected vias, none is on net {net!r}").format(
                    count=len(vias), net=origin_via_net)]
            ))
        if len(candidates) > 1:
            positions = [f"({v.position.x/MM:.3f}, {v.position.y/MM:.3f})" for v in candidates]
            raise ValidationError(format_fatal_error(
                _("--origin-by-via-net {net!r} is ambiguous").format(net=origin_via_net),
                [_("selection contains {count} vias on this net: {pos} — "
                   "refine the selection (keep only one such via) or use "
                   "--origin-by-component-role instead").format(
                       count=len(candidates), pos=positions)]
            ))
        return candidates[0].position

    if origin_component_role is not None:
        for fp in footprints:
            if adapter.get_field_value(fp, ROLE_FIELD_NAME) == origin_component_role:
                if origin_component_pad is None:
                    return fp.position
                pad = adapter.get_pad_by_number(fp, origin_component_pad)
                if pad is None:
                    raise ValidationError(format_fatal_error(
                        _("--origin-by-component-pad {pad!r} not found").format(pad=origin_component_pad),
                        [_("component with role {role!r} ({ref}) has no pad {pad!r} — "
                           "pad numbers are strings as in KiCad").format(
                               role=origin_component_role, ref=fp.reference_field.text.value,
                               pad=origin_component_pad)]
                    ))
                return pad.position
        raise ValidationError(format_fatal_error(
            _("--origin-by-component-role {role!r} not found in selection").format(role=origin_component_role),
            [_("among {count} selected components, none has role {role!r}").format(
                count=len(footprints), role=origin_component_role)]
        ))

    return _bbox_origin(footprints, vias)


def extract_template_from_selection(
    adapter: KiCadBoardAdapter,
    name: str,
    params: Optional[Dict[str, Any]] = None,
    net_template_map: Optional[Dict[str, str]] = None,
    origin_via_net: Optional[str] = None,
    origin_component_role: Optional[str] = None,
    origin_component_pad: Optional[str] = None,
    net_template_role: Optional[Dict[str, str]] = None,
    items: Optional[List[Any]] = None,
    annotations: Optional[List[Tuple[str, str, str]]] = None,
) -> Dict[str, Any]:
    """
    Builds a dict {name: {vias: [...], components: [...], tracks: [...]}}
    ready to be written to YAML under the 'templates' key. Fatal (ValidationError)
    if: nothing suitable is selected, a selected component has no Role field,
    or a role appears twice in the selection.

    items — OPTIONAL explicit list of FootprintInstance/Via/Track (same shape
    adapter.get_selected_items() returns). None (default) — live GUI
    selection, unchanged from before. Explicit — the caller (e.g. a script
    using kicadspoke.explore.Board.select_items()) fully describes what to
    extract instead of requiring a mouse selection in KiCad. Deliberately an
    explicit parameter, not inferred from whether anything is currently
    selected — same principle as ClonePlacement.by_selection (see
    config/models.py): an implicit mode switch here would risk silently
    extracting the wrong thing if a stale selection happens to be active.

    params/net_template_map — both optional and only work as a pair
    (see --param/--net-template in kicadspoke_cli.py): net_template_map is an
    explicit literal‑to‑pattern mapping written once by the user at extraction;
    params are the values that will later resolve the pattern at apply time,
    used here ONLY for verification (see net_resolution.parametrize_net).
    Without net_template_map behaviour is unchanged: via.net stays literal,
    role net_template stays empty.

    origin_via_net/origin_component_role — both optional, mutually exclusive
    (see --origin-by-via-net/--origin-by-component-role in CLI).
    Without them origin is the selection bbox. With them origin is taken from
    the current position of the specific via/component. origin_component_pad is
    ONLY a refinement of origin_component_role (see --origin-by-component-pad):
    without it origin is the component centre, with it the position of the
    specific pad (same principle as anchor_pad in ClonePlacement).

    net_template_role — OPTIONAL, {role: literal_net} (see
    --net-template-role in CLI). Needed only for components with MULTIPLE nets
    from net_template_map on their pads (inductors/ferrite beads/fuses bridging
    two rails) — for those the auto‑inference below cannot choose which net is
    "the role's" (see warning about "N nets from --net-template on pads"), and
    without this parameter net_template remains empty until manual YAML editing.
    No guessing here either: if the role is in net_template_role but the
    specified net is not actually on the component's pads — fatal, not silent.

    annotations — OPTIONAL output parameter (list appended to in place, same
    "explicit opt-in" shape as items above). When given, every case where
    net_template could not be determined unambiguously (see "N nets from
    --net-template on pads" warning below) also appends a
    (role, field_name, hint) tuple, so the caller (kicadspoke_cli.py's
    cmd_extract) can render it as a commented placeholder line in the
    written YAML via render_uncertain_comments() instead of leaving the gap
    only visible in the log.
    """
    params = params or {}
    net_template_role = net_template_role or {}
    net_template_map = dict(net_template_map or {})
    # Auto‑inference for the simple case: if the literal net name EQUALS a
    # param value exactly (not part of a longer string), net_template can be
    # derived automatically — no need for explicit --net-template.
    # Explicit net_template_map entries always take priority.
    for key, value in params.items():
        if value not in net_template_map:
            net_template_map[value] = f"{{{key}}}"
    items = items if items is not None else adapter.get_selected_items()
    footprints = [i for i in items if isinstance(i, FootprintInstance)]
    vias = [i for i in items if isinstance(i, Via)]
    tracks_selected = [i for i in items if isinstance(i, Track)]
    ignored = [i for i in items if not isinstance(i, (FootprintInstance, Via, Track))]

    if ignored:
        logger.warning(_("{count} selected objects — not footprint, via, or track, "
                         "ignored (template only supports these)").format(count=len(ignored)))

    tracks = _filter_tracks_within_selection(tracks_selected, footprints, vias, adapter) \
        if tracks_selected else []
    if len(tracks) < len(tracks_selected):
        logger.info(_("Tracks in selection: {total}, taken into template: {kept} "
                      "(the rest extend beyond the selection, see warning above)")
                    .format(total=len(tracks_selected), kept=len(tracks)))

    if not footprints and not vias and not tracks:
        raise ValidationError(format_fatal_error(
            _("nothing to extract"),
            [_("Nothing is selected (or selected objects are not footprints/vias/tracks) — "
               "select the desired board area in KiCad before running")]
        ))

    problems: List[str] = []
    roles_seen: Dict[str, str] = {}
    for fp in footprints:
        ref = fp.reference_field.text.value
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is None:
            problems.append(_("{ref}: no {field!r} field — every selected component "
                              "must have a Role for template extraction")
                            .format(ref=ref, field=ROLE_FIELD_NAME))
            continue
        if role in roles_seen:
            problems.append(_("role {role!r} appears twice in selection: "
                              "{ref1!r} and {ref2!r} — roles must be unique")
                            .format(role=role, ref1=roles_seen[role], ref2=ref))
            continue
        roles_seen[role] = ref

    if problems:
        raise ValidationError(format_fatal_error(_("problems in current selection"), problems))

    origin = _find_origin(footprints, vias, origin_via_net, origin_component_role,
                          origin_component_pad, adapter)
    origin_desc = (_("via on net {net!r}") if origin_via_net
                   else _("component with role {role!r}") if origin_component_role
                   else _("bbox of selection (lower‑left corner)"))
    origin_desc = origin_desc.format(net=origin_via_net, role=origin_component_role) if '{' in origin_desc else origin_desc
    logger.info(_("Origin ({desc}): ({x:.3f}, {y:.3f}) mm")
                .format(desc=origin_desc, x=origin.x/MM, y=origin.y/MM))

    # Layers — FACT, absolute: template layer = majority layer of selection,
    # components on it inherit without a field, deviating ones get an explicit
    # layer. No relative sides.
    from kipy.board_types import BoardLayer
    back_count = sum(1 for fp in footprints if fp.layer == BoardLayer.BL_B_Cu)
    tpl_is_back = back_count > len(footprints) / 2
    tpl_layer_str = 'B.Cu' if tpl_is_back else 'F.Cu'
    tpl_layer = BoardLayer.BL_B_Cu if tpl_is_back else BoardLayer.BL_F_Cu
    if 0 < back_count < len(footprints):
        logger.info(_("Mixed selection: {back} on B.Cu, {front} on F.Cu; template layer = {layer}, "
                      "deviating components will have explicit layer")
                    .format(back=back_count, front=len(footprints)-back_count, layer=tpl_layer_str))
    logger.info(_("Template layer: {layer}").format(layer=tpl_layer_str))

    components = []
    for fp in footprints:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        along_mm = round((fp.position.x - origin.x) / MM, 4)
        across_mm = round((fp.position.y - origin.y) / MM, 4)
        slot = {
            "role": role,
            "offset_along_mm": along_mm,
            "offset_across_mm": across_mm,
            "angle_deg": fp.orientation.degrees,
        }
        if fp.layer != tpl_layer:
            slot["layer"] = 'F.Cu' if fp.layer == BoardLayer.BL_F_Cu else 'B.Cu'

        if role in net_template_role:
            literal = net_template_role[role]
            fp_nets = sorted({p.net.name for p in adapter.get_footprint_pads(fp)
                              if p.net and p.net.name})
            if literal not in fp_nets:
                raise ValidationError(format_fatal_error(
                    _("--net-template-role for role {role!r} asks for net {literal!r}, "
                      "but it is not on any pad of {ref}").format(role=role, literal=literal,
                                                                   ref=fp.reference_field.text.value),
                    [_("actual nets on pads: {nets} — check typo in "
                       "--net-template-role or in the role itself").format(nets=fp_nets)]
                ))
            if literal not in net_template_map:
                raise ValidationError(format_fatal_error(
                    _("--net-template-role for role {role!r} asks for net {literal!r}, "
                      "which is not in net_template_map").format(role=role, literal=literal),
                    [_("add {literal!r} to --net-template/net_template (or to params "
                       "if it equals a parameter value) — otherwise there is no pattern to build")
                     .format(literal=literal)]
                ))
            slot["net_template"] = parametrize_net(literal, net_template_map, params)
        elif net_template_map:
            fp_nets = sorted({p.net.name for p in adapter.get_footprint_pads(fp)
                              if p.net and p.net.name})
            mapped = [n for n in fp_nets if n in net_template_map]
            if len(mapped) == 1:
                slot["net_template"] = parametrize_net(mapped[0], net_template_map, params)
            elif len(mapped) > 1:
                hint = _("could not determine automatically — {count} matching nets on pads "
                         "({nets}) — fill in manually or use --net-template-role {role}=<net>") \
                    .format(count=len(mapped), nets=mapped, role=role)
                logger.warning(_("  {ref} (role {role}): {count} nets from --net-template on pads "
                                 "({nets}) — net_template not set, fill it manually in the "
                                 "resulting YAML, or use --net-template-role {role}=<net> in advance")
                               .format(ref=fp.reference_field.text.value, role=role,
                                       count=len(mapped), nets=mapped))
                if annotations is not None:
                    annotations.append((role, "net_template", hint))
        components.append(slot)
        logger.debug(_("  {ref} (role {role}): along={along}, across={across}, angle={angle}{layer}{net}")
                     .format(ref=fp.reference_field.text.value, role=role,
                             along=along_mm, across=across_mm, angle=fp.orientation.degrees,
                             layer=_(", layer={layer}").format(layer=slot.get('layer')) if 'layer' in slot else "",
                             net=_(", net_template={nt}").format(nt=slot.get('net_template')) if 'net_template' in slot else ""))

    spoke_vias = []
    for v in vias:
        along_mm = round((v.position.x - origin.x) / MM, 4)
        across_mm = round((v.position.y - origin.y) / MM, 4)
        via_net = v.net.name if v.net else None
        if via_net is not None and net_template_map:
            via_net = parametrize_net(via_net, net_template_map, params)
        spoke_vias.append({
            "offset_along_mm": along_mm,
            "offset_across_mm": across_mm,
            "net": via_net,
            "drill_mm": round(v.drill_diameter / MM, 4),
            "diameter_mm": round(v.diameter / MM, 4),
        })
        logger.debug(_("  via: along={along}, across={across}, net={net}")
                     .format(along=along_mm, across=across_mm, net=via_net))

    spoke_tracks = []
    for t in tracks:
        start_along_mm = round((t.start.x - origin.x) / MM, 4)
        start_across_mm = round((t.start.y - origin.y) / MM, 4)
        end_along_mm = round((t.end.x - origin.x) / MM, 4)
        end_across_mm = round((t.end.y - origin.y) / MM, 4)
        track_net = t.net.name if t.net else None
        if track_net is not None and net_template_map:
            track_net = parametrize_net(track_net, net_template_map, params)
        entry = {
            "start_along_mm": start_along_mm,
            "start_across_mm": start_across_mm,
            "end_along_mm": end_along_mm,
            "end_across_mm": end_across_mm,
            "width_mm": round(t.width / MM, 4),
            "net": track_net,
        }
        if t.layer != tpl_layer:
            entry["layer"] = 'F.Cu' if t.layer == BoardLayer.BL_F_Cu else 'B.Cu'
        spoke_tracks.append(entry)
        logger.debug(_("  track: ({sx},{sy}) -> ({ex},{ey}), net={net}{layer}")
                     .format(sx=start_along_mm, sy=start_across_mm,
                             ex=end_along_mm, ey=end_across_mm, net=track_net,
                             layer=_(", layer={layer}").format(layer=entry['layer']) if 'layer' in entry else ""))

    logger.info(_("Extracted template {name!r}: {comp} components, {vias} spoke‑level vias, {tracks} tracks")
                .format(name=name, comp=len(components), vias=len(spoke_vias), tracks=len(spoke_tracks)))
    result = {"vias": spoke_vias, "components": components, "tracks": spoke_tracks, "layer": tpl_layer_str}
    return {name: result}


def render_uncertain_comments(yaml_text: str, name: str,
                               annotations: List[Tuple[str, str, str]]) -> str:
    """
    Post-processes yaml.dump() output for cmd_extract: for every
    (role, field, hint) in annotations, inserts a commented-out placeholder
    line ("# field: hint") right after the component block for that role,
    scoped to the section under the top-level `name:` key only —
    cmd_extract's `existing` dict may hold OTHER, previously extracted
    templates in the same output file, and a role from THIS extraction must
    never accidentally match a same-named role belonging to a different
    template.

    Text-based, not a YAML-aware round-trip: the only producer of this text
    is our own yaml.dump(sort_keys=False, default_flow_style=False) call in
    cmd_extract, so the indentation shape (list items at indent 2, their
    fields at indent 4) is known and stable — a full round-trip library
    (ruamel.yaml) would be overkill for annotating output we generate
    ourselves.
    """
    if not annotations:
        return yaml_text
    lines = yaml_text.splitlines(keepends=True)

    def body(line: str) -> str:
        return line.rstrip("\n")

    name_pattern = re.compile(r'^(["\']?)' + re.escape(name) + r'\1:\s*$')
    start = None
    for i, line in enumerate(lines):
        if name_pattern.match(body(line)):
            start = i + 1
            break
    if start is None:
        return yaml_text
    end = len(lines)
    for i in range(start, len(lines)):
        b = body(lines[i])
        if b.strip() and not b[0].isspace():
            end = i
            break

    block = lines[start:end]
    for role, field, hint in annotations:
        role_pattern = re.compile(r'^  - role: (["\']?)' + re.escape(role) + r'\1\s*$')
        role_idx = next((i for i, line in enumerate(block)
                         if role_pattern.match(body(line))), None)
        if role_idx is None:
            continue
        insert_at = len(block)
        for i in range(role_idx + 1, len(block)):
            b = body(block[i])
            leading = len(b) - len(b.lstrip(' '))
            if b.strip() and leading < 4:
                insert_at = i
                break
        block.insert(insert_at, f"    # {field}: {hint}\n")

    return "".join(lines[:start] + block + lines[end:])