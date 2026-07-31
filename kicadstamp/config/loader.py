# kicadstamp/config/loader.py
from typing import Tuple
"""
config/loader.py — all YAML loading/validation logic for dataclasses
from config/models.py: load_config() (entry point) and all _load_* functions.
Split from monolithic config.py by the same refactoring as models.py.
"""
import difflib
import logging
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import yaml

from ..exceptions import ValidationError, format_fatal_error, check_unknown_keys
from ..sheet_names import build_sheet_name_map
from ..runtime_context import RuntimeContext
from .includes import resolve_includes
from .models import (
    ThermalViaArrayConfig, TemplateVia, TemplateComponentSlot, TemplateTrack,
    Cell, ManualSpoke, Rule, ClonePlacement, Config, rule_effective_name,
)
from .points import Point
from ..i18n import _

logger = logging.getLogger(__name__)

def _load_template_via(data: Dict[str, Any]) -> TemplateVia:
    net = data.get('net')
    if net is not None and not isinstance(net, str):
        raise ValidationError(format_fatal_error(
            _("via.net must be a string, not {type}").format(type=type(net).__name__),
            [_("got: {net!r} (offset_along_mm={along}, offset_across_mm={across})").format(
                net=net, along=data.get('offset_along_mm'), across=data.get('offset_across_mm')),
             _("looks like broken YAML – e.g. net_overrides accidentally nested under "
               "this via's net instead of being a top-level field of clone_placement "
               "(net_overrides is a sibling of cell/params, not under via)")]
        ))
    return TemplateVia(
        offset_along_mm=data.get('offset_along_mm', 0.0),
        offset_across_mm=data.get('offset_across_mm', 0.0),
        net=net,
        drill_mm=data.get('drill_mm', 0.3),
        diameter_mm=data.get('diameter_mm', 0.6),
    )


def _load_template_track(data: Dict[str, Any]) -> TemplateTrack:
    net = data.get('net')
    if net is not None and not isinstance(net, str):
        raise ValidationError(format_fatal_error(
            _("track.net must be a string, not {type}").format(type=type(net).__name__),
            [_("got: {net!r} (start_along_mm={along}, start_across_mm={across})").format(
                net=net, along=data.get('start_along_mm'), across=data.get('start_across_mm')),
             _("looks like broken YAML – e.g. placeholder like {{NET}} without quotes: "
               "YAML reads it as flow-mapping, not a string; use quotes: net: '{{NET}}'")]
        ))
    layer = data.get('layer')
    _check_layer_value(layer, _("on track"))
    return TemplateTrack(
        start_along_mm=data.get('start_along_mm', 0.0),
        start_across_mm=data.get('start_across_mm', 0.0),
        end_along_mm=data.get('end_along_mm', 0.0),
        end_across_mm=data.get('end_across_mm', 0.0),
        width_mm=data.get('width_mm', 0.25),
        net=net,
        layer=layer,
    )


def _check_layer_value(value, where: str):
    if value is not None and value not in ('F.Cu', 'B.Cu'):
        raise ValidationError(format_fatal_error(
            _("invalid layer={value!r} {where}").format(value=value, where=where),
            [_("layer must be absolute: 'F.Cu' or 'B.Cu'")]
        ))


def _load_template_component_slot(data: Dict[str, Any]) -> TemplateComponentSlot:
    if 'side' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'side' in slot {role!r}").format(role=data.get('role')),
            [_("relative 'side' is deprecated (see discussion v116): layer is now "
               "absolute – write layer: F.Cu or layer: B.Cu, or remove the field "
               "to inherit the cell layer")]
        ))
    layer = data.get('layer')
    _check_layer_value(layer, _("on slot {role!r}").format(role=data.get('role')))
    return TemplateComponentSlot(
        role=data['role'],
        offset_along_mm=data.get('offset_along_mm', 0.0),
        offset_across_mm=data.get('offset_across_mm', 0.0),
        angle_deg=data.get('angle_deg', 0.0),
        vias=[_load_template_via(v) for v in data.get('vias', [])],
        net_template=data.get('net_template'),
        layer=layer,
    )


def _load_cell(name: str, data: Dict[str, Any]) -> Cell:
    components = [_load_template_component_slot(c) for c in data.get('components', [])]

    roles = [c.role for c in components]
    duplicates = {r for r in roles if roles.count(r) > 1}
    if duplicates:
        raise ValidationError(format_fatal_error(
            _("role appears twice in cell {name!r}").format(name=name),
            [_("role {role!r} appears {count} times in components of this cell – "
               "roles inside a cell must be unique (see anchor_id/cell_name/role "
               "in the placement registry)").format(role=r, count=roles.count(r))
             for r in sorted(duplicates)]
        ))

    if 'reference_side' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'reference_side' in cell {name!r}").format(name=name),
            [_("renamed (see discussion v116): use layer: F.Cu or layer: B.Cu – "
               "absolute cell layer, as extracted")]
        ))
    layer = data.get('layer', 'F.Cu')
    _check_layer_value(layer, _("in cell {name!r}").format(name=name))

    return Cell(
        name=name,
        vias=[_load_template_via(v) for v in data.get('vias', [])],
        components=components,
        tracks=[_load_template_track(t) for t in data.get('tracks', [])],
        layer=layer,
    )


_POINT_KNOWN_KEYS = {
    'anchor_ref', 'anchor_role', 'anchor_sheet', 'anchor_cluster', 'anchor_pad',
    'anchor_point', 'xy', 'shift_x_mm', 'shift_y_mm',
}


def _load_point(name: str, data: Dict[str, Any]) -> Point:
    check_unknown_keys(data, _POINT_KNOWN_KEYS,
                       _("unknown fields in point {name!r}").format(name=name))

    anchor_ref = data.get('anchor_ref')
    anchor_role = data.get('anchor_role')
    anchor_sheet = data.get('anchor_sheet')
    anchor_cluster = data.get('anchor_cluster')
    anchor_pad = data.get('anchor_pad')
    anchor_point = data.get('anchor_point')
    xy = data.get('xy')

    # Exactly one "base": (anchor_ref or anchor_role) / anchor_point / xy.
    base_kind_count = sum([
        anchor_ref is not None or anchor_role is not None,
        anchor_point is not None,
        xy is not None,
    ])
    if base_kind_count == 0:
        raise ValidationError(format_fatal_error(
            _("point {name!r} has no anchor").format(name=name),
            [_("set exactly one of: anchor_ref/anchor_role (+ optional anchor_sheet/"
               "anchor_cluster/anchor_pad), anchor_point (chain to another point), "
               "or xy (literal absolute coordinate)")]
        ))
    if base_kind_count > 1:
        raise ValidationError(format_fatal_error(
            _("point {name!r} has more than one anchor base").format(name=name),
            [_("anchor_ref/anchor_role, anchor_point, and xy are mutually exclusive — "
               "pick exactly one way to define this point's base position")]
        ))
    if anchor_ref is not None and anchor_role is not None:
        raise ValidationError(format_fatal_error(
            _("anchor_ref and anchor_role together in point {name!r}").format(name=name),
            [_("mutually exclusive: either by refdes (anchor_ref) or by Role field "
               "(anchor_role), not both")]
        ))
    if anchor_sheet is not None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            _("anchor_sheet without anchor_role in point {name!r}").format(name=name),
            [_("anchor_sheet only narrows ambiguity of anchor_role, it is not an anchor itself")]
        ))
    if anchor_pad is not None and anchor_ref is None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            _("anchor_pad without anchor_ref/anchor_role in point {name!r}").format(name=name),
            [_("anchor_pad={pad!r} is set but no anchor specified").format(pad=anchor_pad)]
        ))

    shift_x_mm = data.get('shift_x_mm', 0.0)
    shift_y_mm = data.get('shift_y_mm', 0.0)
    if xy is not None and (shift_x_mm or shift_y_mm):
        raise ValidationError(format_fatal_error(
            _("shift on a literal xy point {name!r}").format(name=name),
            [_("xy is already an absolute coordinate — edit it directly instead of "
               "combining it with shift_x_mm/shift_y_mm")]
        ))
    if xy is not None:
        if not (isinstance(xy, (list, tuple)) and len(xy) == 2):
            raise ValidationError(format_fatal_error(
                _("xy must be a 2-element [x, y] list in point {name!r}").format(name=name),
                [_("got: {xy!r}").format(xy=xy)]
            ))
        xy = (float(xy[0]), float(xy[1]))

    return Point(
        name=name,
        anchor_ref=anchor_ref,
        anchor_role=anchor_role,
        anchor_sheet=anchor_sheet,
        anchor_cluster=anchor_cluster,
        anchor_pad=str(anchor_pad) if anchor_pad is not None else None,
        anchor_point=anchor_point,
        xy=xy,
        shift_x_mm=shift_x_mm,
        shift_y_mm=shift_y_mm,
    )


def _point_is_footprint_eligible(points: Dict[str, Point], name: str, _visited=None) -> bool:
    """True if the point named `name` (transitively, through any anchor_point
    chain) resolves to a live footprint with no shift applied anywhere along
    the way — the requirement for Rule/ThermalViaArrayConfig's anchor_point,
    which need a component to look up named pads from (spoke.pad/tva.pad),
    not just a coordinate (see ThermalViaArrayConfig.anchor_point docstring
    in config/models.py). Pure static walk over points: definitions — no live
    board access, shift/xy are literal YAML values. A cycle in the walk just
    returns False here (not fatal) — the precise, definitive cycle error is
    raised at RUNTIME by dependency_order.py's Kahn's algorithm; this check
    does not duplicate that detection, it only needs a bounded walk."""
    if _visited is None:
        _visited = set()
    if name in _visited:
        return False
    _visited.add(name)
    point = points.get(name)
    if point is None:
        return False  # unknown name — reported separately, see _check_anchor_point
    if point.shift_x_mm or point.shift_y_mm:
        return False
    if point.xy is not None:
        return False
    if point.anchor_point is not None:
        return _point_is_footprint_eligible(points, point.anchor_point, _visited)
    return point.anchor_ref is not None or point.anchor_role is not None


_MANUAL_SPOKE_KNOWN_KEYS = {
    'pad', 'cell', 'shift_x_mm', 'shift_y_mm', 'rotation_deg',
    'retired', 'cluster', 'skip',
}


def _load_manual_spoke(data: Dict[str, Any], rule_label: str) -> ManualSpoke:
    check_unknown_keys(data, _MANUAL_SPOKE_KNOWN_KEYS,
                       _("unknown fields in spoke (pad {pad!r}) of rule (net {net!r})")
                       .format(pad=data.get('pad', '?'), net=rule_label))
    return ManualSpoke(
        pad=data['pad'],
        cell=data['cell'],
        shift_x_mm=data.get('shift_x_mm', 0.0),
        shift_y_mm=data.get('shift_y_mm', 0.0),
        rotation_deg=data.get('rotation_deg', 0.0),
        retired=data.get('retired', False),
        cluster=data.get('cluster'),
        skip=data.get('skip', False),
    )


_RULE_KNOWN_KEYS = {
    'net', 'spokes', 'anchor_ref', 'anchor_role', 'anchor_sheet',
    'anchor_cluster', 'anchor_point', 'name', 'retired', 'skip',
}


_THERMAL_VIA_ARRAY_KNOWN_KEYS = {
    'retired', 'anchor_ref', 'anchor_role', 'anchor_sheet', 'anchor_cluster',
    'anchor_point', 'pad', 'net', 'rows', 'cols', 'margin_mm', 'pattern',
    'drill_mm', 'diameter_mm', 'name', 'skip',
}


_CLONE_PLACEMENT_KNOWN_KEYS = {
    'name', 'cell', 'role', 'xy', 'rotation_deg',
    'nets', 'params', 'net_overrides', 'retired', 'skip', 'ignore_selection',
    'anchor_ref', 'anchor_pad', 'anchor_role', 'anchor_sheet', 'anchor_cluster',
    'anchor_point', 'layer', 'mirror', 'refs', 'by_selection',
    'side',  # deprecated – recognised separately to give a migration message
    'origin_x_mm', 'origin_y_mm',  # deprecated – recognised to give a migration message
}


def _load_clone_placement(data: Dict[str, Any]) -> ClonePlacement:
    name = data.get('name', '?')
    if not data.get('name'):
        raise ValidationError(format_fatal_error(
            _("clone_placement without name"),
            [_("every clone_placement must have a name – used in --only "
               "(kicadstamp_cli.py) for isolated runs; write name: <string>. "
               "Previously missing name would silently substitute '?' – that was a bug")]
        ))
    check_unknown_keys(data, _CLONE_PLACEMENT_KNOWN_KEYS,
                       _("unknown fields in clone_placement {name!r}").format(name=name),
                       extra_hint=_(" (e.g. 'pad' won't work; use 'anchor_pad')"))

    anchor_ref = data.get('anchor_ref')
    anchor_pad = data.get('anchor_pad')
    anchor_role = data.get('anchor_role')
    anchor_sheet = data.get('anchor_sheet')
    anchor_cluster = data.get('anchor_cluster')
    anchor_point = data.get('anchor_point')

    cell = data.get('cell')
    role = data.get('role')
    if cell is not None and role is not None:
        raise ValidationError(format_fatal_error(
            _("cell and role together in clone_placement {name!r}").format(name=name),
            [_("these are mutually exclusive ways to define the content: "
               "either a ready-made cell (cell), or a single-component placement "
               "by role (role), not both")]
        ))
    if cell is None and role is None:
        raise ValidationError(format_fatal_error(
            _("neither cell nor role set in clone_placement {name!r}").format(name=name),
            [_("need either cell: <name from cells:>, or role: <ROLE> for "
               "a single-component placement without a separate cell file")]
        ))

    if anchor_ref is not None and anchor_role is not None:
        raise ValidationError(format_fatal_error(
            _("anchor_ref and anchor_role together in clone_placement {name!r}").format(name=name),
            [_("these are mutually exclusive ways to define the anchor – either by refdes "
               "(anchor_ref) or by Role field (anchor_role), not both")]
        ))

    if anchor_sheet is not None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            _("anchor_sheet without anchor_role in clone_placement {name!r}").format(name=name),
            [_("anchor_sheet={sheet!r} is set but anchor_role is missing – "
               "anchor_sheet only narrows ambiguity of anchor_role, it is not an anchor itself")
             .format(sheet=anchor_sheet)]
        ))

    if anchor_point is not None and (anchor_ref is not None or anchor_role is not None):
        raise ValidationError(format_fatal_error(
            _("anchor_point together with anchor_ref/anchor_role in clone_placement {name!r}")
            .format(name=name),
            [_("anchor_point={point!r} names a points: entry that already carries its own "
               "anchor — mutually exclusive with anchor_ref/anchor_role").format(point=anchor_point)]
        ))
    if anchor_point is not None and anchor_pad is not None:
        raise ValidationError(format_fatal_error(
            _("anchor_point together with anchor_pad in clone_placement {name!r}").format(name=name),
            [_("anchor_point already resolves to a full position — anchor_pad has no "
               "meaning on top of it; set anchor_pad on the points: entry itself instead")]
        ))

    if anchor_pad is not None and anchor_ref is None and anchor_role is None and anchor_point is None:
        raise ValidationError(format_fatal_error(
            _("anchor_pad without anchor_ref/anchor_role in clone_placement {name!r}").format(name=name),
            [_("anchor_pad={pad!r} is set but no anchor specified – "
               "use anchor_ref: IC1 or anchor_role: SOME_ROLE").format(pad=anchor_pad)]
        ))

    has_anchor = anchor_ref is not None or anchor_role is not None or anchor_point is not None

    if not has_anchor and 'xy' not in data:
        raise ValidationError(format_fatal_error(
            _("no anchor and no absolute coordinates in clone_placement {name!r}").format(name=name),
            [_("either set xy: [x, y] (absolute point on board), "
               "or anchor_ref/anchor_role (+ optionally anchor_pad), or anchor_point, "
               "for anchor‑based placement")]
        ))

    if 'side' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'side' in clone_placement {name!r}").format(name=name),
            [_("side is now set by an explicit pair: layer: F.Cu|B.Cu (where we place – fact) "
               "+ mirror: true (how we place – operation, only meaningful when the layer changes "
               "relative to the cell)")]
        ))
    if 'origin_x_mm' in data or 'origin_y_mm' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated fields 'origin_x_mm'/'origin_y_mm' in clone_placement {name!r}").format(name=name),
            [_("renamed to xy: [x, y] — write xy: [{x}, {y}] instead")
             .format(x=data.get('origin_x_mm', 0.0), y=data.get('origin_y_mm', 0.0))]
        ))

    xy_raw = data.get('xy')
    if xy_raw is not None:
        if not (isinstance(xy_raw, (list, tuple)) and len(xy_raw) == 2):
            raise ValidationError(format_fatal_error(
                _("xy must be a 2-element [x, y] list in clone_placement {name!r}").format(name=name),
                [_("got: {xy!r}").format(xy=xy_raw)]
            ))
        xy = (float(xy_raw[0]), float(xy_raw[1]))
    else:
        xy = (0.0, 0.0)

    by_selection = bool(data.get('by_selection', False))
    nets = data.get('nets', {}) or {}
    if by_selection and nets:
        raise ValidationError(format_fatal_error(
            _("by_selection: true with non-empty nets in clone_placement {name!r}").format(name=name),
            [_("nets is an explicit role->net mapping for 'by nets' mode; in selection mode "
               "roles are resolved by mouse selection, not by nets – nets is meaningless here. "
               "Either remove nets, or remove by_selection: true")]
        ))

    layer = data.get('layer')
    _check_layer_value(layer, _("in clone_placement {name!r}").format(name=name))

    return ClonePlacement(
        name=name,
        cell=cell,
        role=role,
        xy=xy,
        rotation_deg=data.get('rotation_deg', 0.0),
        nets=nets,
        params=data.get('params', {}) or {},
        net_overrides=data.get('net_overrides', {}) or {},
        retired=data.get('retired', False),
        skip=data.get('skip', False),
        ignore_selection=data.get('ignore_selection', False),
        anchor_ref=anchor_ref,
        anchor_pad=str(anchor_pad) if anchor_pad is not None else None,
        anchor_role=anchor_role,
        anchor_sheet=anchor_sheet,
        anchor_cluster=anchor_cluster,
        anchor_point=anchor_point,
        layer=layer,
        mirror=bool(data.get('mirror', False)),
        refs=data.get('refs', {}) or {},
        by_selection=by_selection,
    )


def load_config(path: str) -> Tuple[Config, RuntimeContext]:
    logger.info(_("Loading configuration from {path}").format(path=path))
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    data = resolve_includes(path, data)

    if 'target_ref' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'target_ref' at root of config"),
            [_("global target_ref has been removed (see discussion v117): each spoke "
               "rule now has its own anchor – write anchor_ref: <ref> inside the rule "
               "in rules; thermal_via_array has its own anchor_ref field")]
        ))
    if 'side' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'side' at root of config"),
            [_("use layer: F.Cu or layer: B.Cu instead (layer for ManualSpoke rules; "
               "back -> B.Cu)")]
        ))
    root_layer = data.get('layer', 'F.Cu')
    _check_layer_value(root_layer, _("at root of config"))

    tva_data = data.get('thermal_via_array', {})
    if 'target_ref' in tva_data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'target_ref' in thermal_via_array"),
            [_("renamed for consistency: use anchor_ref")]
        ))
    if tva_data and not tva_data.get('name'):
        raise ValidationError(format_fatal_error(
            _("thermal_via_array without name"),
            [_("thermal_via_array must have a name – used in --only "
               "(kicadstamp_cli.py) for isolated runs; write name: <any understandable string>, "
               "e.g. name: fpga_thermal")]
        ))
    check_unknown_keys(tva_data, _THERMAL_VIA_ARRAY_KNOWN_KEYS,
                       _("unknown fields in thermal_via_array"))
    if tva_data.get('anchor_point') is not None and (
            tva_data.get('anchor_ref') is not None or tva_data.get('anchor_role') is not None):
        raise ValidationError(format_fatal_error(
            _("anchor_point together with anchor_ref/anchor_role in thermal_via_array"),
            [_("anchor_point={point!r} names a points: entry that already carries its own "
               "anchor — mutually exclusive with anchor_ref/anchor_role")
             .format(point=tva_data.get('anchor_point'))]
        ))
    thermal_via = ThermalViaArrayConfig(
        # Absent thermal_via_array: section (tva_data == {}) must keep meaning
        # "nothing configured, do nothing" — same sentinel convention as the
        # name-check above, not the class-level default (which is False,
        # unified with Rule/ManualSpoke/ClonePlacement for the case where the
        # section IS present). See handoff_2026_07_31_consolidated.md §5 —
        # found 2026-07-31: naive `tva_data.get('retired', False)` made every
        # config without this section fatal on apply (no anchor_ref/anchor_role
        # either, since those default to None too).
        retired=tva_data.get('retired', not tva_data),
        anchor_ref=tva_data.get('anchor_ref'),
        anchor_role=tva_data.get('anchor_role'),
        anchor_sheet=tva_data.get('anchor_sheet'),
        anchor_cluster=tva_data.get('anchor_cluster'),
        anchor_point=tva_data.get('anchor_point'),
        pad=tva_data.get('pad', ''),
        net=tva_data.get('net', 'GND'),
        rows=tva_data.get('rows', 4),
        cols=tva_data.get('cols', 4),
        margin_mm=tva_data.get('margin_mm', 0.5),
        pattern=tva_data.get('pattern', 'grid'),
        drill_mm=tva_data.get('drill_mm', 0.3),
        diameter_mm=tva_data.get('diameter_mm', 0.5),
        name=tva_data.get('name'),
        skip=tva_data.get('skip', False),
    )

    cells_data = dict(data.get('cells', {}) or {})

    templates_file = data.get('templates_file')
    template_files = data.get('template_files') or []
    if not isinstance(template_files, list):
        raise ValidationError(format_fatal_error(
            _("template_files must be a list, got {type}").format(type=type(template_files).__name__),
            [_("template_files: is a YAML list of paths ('- templates/a.yaml'); "
               "for a single file use templates_file: <path> instead")]
        ))
    external_files = ([templates_file] if templates_file else []) + list(template_files)

    # Each external file is the RAW extract() shape ({name: {...}}, no
    # 'cells:' wrapper — unlike include:, which expects one). Merged
    # among THEMSELVES with fatal on a repeated name (independent files —
    # a collision is far more likely a copy-paste mistake than an
    # intentional override, same philosophy as include:'s _DICT_SECTIONS).
    # Inline cells: in this config file still overrides silently on top
    # of all of them, unchanged from templates_file's original behaviour.
    external_cells: Dict[str, Any] = {}
    for ext_file in external_files:
        cells_path = Path(path).parent / ext_file
        if not cells_path.exists():
            raise ValidationError(format_fatal_error(
                _("templates file {file!r} not found").format(file=ext_file),
                [_("expected at {path} (relative to the config file itself, "
                   "not the current working directory)").format(path=cells_path)]
            ))
        with open(cells_path, 'r', encoding='utf-8') as f:
            if cells_path.suffix.lower() == '.json':
                file_cells = json.load(f)
            else:
                file_cells = yaml.safe_load(f) or {}
        for name, cdata in (file_cells or {}).items():
            if name in external_cells:
                raise ValidationError(format_fatal_error(
                    _("duplicate cell {name!r} across templates_file/template_files").format(name=name),
                    [_("defined in more than one external templates file (templates_file "
                       "and/or an entry of template_files) — external files are meant to "
                       "be independent, a repeated name is far more likely a mistake than "
                       "an intentional override; inline cells: in the config itself "
                       "CAN still override an external one, that is unaffected")]
                ))
            external_cells[name] = cdata

    merged = dict(external_cells)
    merged.update(cells_data)
    cells_data = merged
    if external_files:
        logger.info(_("Cells from {files}: {count_ext}, plus inline: {count_inline}")
                    .format(files=external_files, count_ext=len(external_cells),
                            count_inline=len(data.get('cells', {}) or {})))
    cells = {name: _load_cell(name, cdata) for name, cdata in cells_data.items()}

    points_data = dict(data.get('points', {}) or {})
    points = {name: _load_point(name, pdata) for name, pdata in points_data.items()}

    rules = []
    for rule_data in data.get('rules', []):
        rule_net = rule_data.get('net')
        check_unknown_keys(rule_data, _RULE_KNOWN_KEYS,
                           _("unknown fields in rule (net {net!r})").format(net=rule_net))
        anchor_ref = rule_data.get('anchor_ref')
        anchor_role = rule_data.get('anchor_role')
        anchor_sheet = rule_data.get('anchor_sheet')
        anchor_cluster = rule_data.get('anchor_cluster')
        anchor_point = rule_data.get('anchor_point')

        if anchor_ref and anchor_role:
            raise ValidationError(format_fatal_error(
                _("anchor_ref and anchor_role together in rule (net {net!r})").format(net=rule_net),
                [_("mutually exclusive: either by refdes (anchor_ref) or by Role field "
                   "(anchor_role), not both")]
            ))
        if anchor_sheet and not anchor_role:
            raise ValidationError(format_fatal_error(
                _("anchor_sheet without anchor_role in rule (net {net!r})").format(net=rule_net),
                [_("anchor_sheet only narrows ambiguity of anchor_role, it is not an anchor itself")]
            ))
        if anchor_point and (anchor_ref or anchor_role):
            raise ValidationError(format_fatal_error(
                _("anchor_point together with anchor_ref/anchor_role in rule (net {net!r})")
                .format(net=rule_net),
                [_("anchor_point={point!r} names a points: entry that already carries its own "
                   "anchor — mutually exclusive with anchor_ref/anchor_role").format(point=anchor_point)]
            ))
        if not anchor_ref and not anchor_role and not anchor_point:
            raise ValidationError(format_fatal_error(
                _("rule (net {net!r}) without anchor_ref/anchor_role/anchor_point").format(net=rule_net),
                [_("a spoke rule must have an anchor – anchor_ref: <ref> (component whose "
                   "pads are listed in spokes), anchor_role: <ROLE> (survives re‑annotation), "
                   "or anchor_point: <name from points:>")]
            ))
        spokes = [_load_manual_spoke(spoke_data, rule_net) for spoke_data in rule_data.get('spokes', [])]
        rules.append(Rule(net=rule_net, spokes=spokes, anchor_ref=anchor_ref,
                          anchor_role=anchor_role, anchor_sheet=anchor_sheet,
                          anchor_cluster=anchor_cluster, anchor_point=anchor_point,
                          name=rule_data.get('name'),
                          retired=rule_data.get('retired', False),
                          skip=rule_data.get('skip', False)))

    # Fatal on collision: two rules resolving to the same --only identity
    # (same net, neither disambiguated with an explicit name) would silently
    # both match the same --only call — catch it at load time, not at --only
    # time, and point at exactly which rules collided.
    seen_names: Dict[str, List[str]] = {}
    for rule in rules:
        seen_names.setdefault(rule_effective_name(rule), []).append(
            rule.anchor_ref or rule.anchor_role or "?"
        )
    for effective_name, anchors in seen_names.items():
        if len(anchors) > 1:
            raise ValidationError(format_fatal_error(
                _("{count} rules resolve to the same --only identity {name!r} "
                  "(anchors: {anchors})").format(count=len(anchors), name=effective_name,
                                                  anchors=", ".join(anchors)),
                [_("give at least one of them an explicit name: to disambiguate "
                   "(e.g. name: {name}_a) – --only cannot tell them apart otherwise")
                 .format(name=effective_name)]
            ))

    clone_placements = [_load_clone_placement(cp) for cp in data.get('clone_placements', [])]

    # Cross‑validation of layer/mirror
    for cp in clone_placements:
        cell = cells.get(cp.cell)
        if cell is None:
            continue
        placement_layer = cp.layer if cp.layer is not None else cell.layer
        layer_changed = placement_layer != cell.layer
        if cp.mirror and not layer_changed:
            raise ValidationError(format_fatal_error(
                _("mirror without layer change in clone_placement {name!r}").format(name=cp.name),
                [_("cell {cell!r} is on {cell_layer}, placement layer is {place_layer} – "
                   "mirror without changing side is physically meaningless: either set layer to "
                   "{opposite}, or remove mirror").format(
                       cell=cp.cell, cell_layer=cell.layer, place_layer=placement_layer,
                       opposite='B.Cu' if cell.layer == 'F.Cu' else 'F.Cu')]
            ))
        if layer_changed and not cp.mirror:
            raise ValidationError(format_fatal_error(
                _("layer changed without mirror in clone_placement {name!r}").format(name=cp.name),
                [_("cell {cell!r} is on {cell_layer}, placement layer is {place_layer} – "
                   "flipped footprints on non‑flipped sites are nonsense; add mirror: true, "
                   "or remove the layer override").format(
                       cell=cp.cell, cell_layer=cell.layer, place_layer=placement_layer)]
            ))

    # Cross-validation of anchor_point references — every value must name an
    # existing points: entry; Rule/thermal_via_array additionally need a
    # footprint-eligible target (see _point_is_footprint_eligible), because
    # they look up a specific named pad on the resolved component
    # (spoke.pad/tva.pad) — a bare coordinate doesn't work for them.
    # ClonePlacement and Point-to-Point chains only ever need a coordinate,
    # so any point (shifted, xy-literal, or not) is fine there.
    def _check_anchor_point(owner_label: str, anchor_point: Optional[str], needs_footprint: bool):
        if anchor_point is None:
            return
        if anchor_point not in points:
            suggestion = difflib.get_close_matches(anchor_point, sorted(points.keys()), n=1)
            hint = (_(" (did you mean {suggestion!r}?)").format(suggestion=suggestion[0])
                    if suggestion else "")
            raise ValidationError(format_fatal_error(
                _("{owner}: anchor_point {name!r} not found in points:{hint}")
                .format(owner=owner_label, name=anchor_point, hint=hint),
                [_("known points: {names}").format(names=sorted(points.keys()))]
            ))
        if needs_footprint and not _point_is_footprint_eligible(points, anchor_point):
            raise ValidationError(format_fatal_error(
                _("{owner}: anchor_point {name!r} has no footprint to anchor on")
                .format(owner=owner_label, name=anchor_point),
                [_("point {name!r} has a shift, is xy-literal, or chains to one that does — "
                   "{owner} needs a live component to look up a specific pad from, a bare "
                   "coordinate is not enough. Use this point with a clone_placement instead, "
                   "or give it shift_x_mm=0/shift_y_mm=0 and no xy")
                 .format(name=anchor_point, owner=owner_label)]
            ))

    for pname, point in points.items():
        _check_anchor_point(_("point {name!r}").format(name=pname), point.anchor_point,
                            needs_footprint=False)
    for rule in rules:
        _check_anchor_point(_("rule (net {net!r})").format(net=rule.net), rule.anchor_point,
                            needs_footprint=True)
    for cp in clone_placements:
        _check_anchor_point(_("clone_placement {name!r}").format(name=cp.name), cp.anchor_point,
                            needs_footprint=False)
    _check_anchor_point(_("thermal_via_array"), thermal_via.anchor_point, needs_footprint=True)

    schematic_dir = data.get('schematic_dir')
    schematic_files = data.get('schematic_files', []) or []
    sheet_names = build_sheet_name_map(path, schematic_dir, schematic_files)

    registry_path = data.get('registry_path')
    track_registry_path = data.get('track_registry_path')
    if registry_path:
        registry_path = str(Path(path).parent / registry_path)
    if track_registry_path:
        track_registry_path = str(Path(path).parent / track_registry_path)

    log_file = data.get('log_file')
    if log_file:
        log_file = str(Path(path).parent / log_file)

    ctx = RuntimeContext(sheet_names=sheet_names)

    cfg = Config(
        layer=root_layer,
        cells=cells,
        points=points,
        thermal_via_array=thermal_via,
        rules=rules,
        clone_placements=clone_placements,
        place_components=data.get('place_components', True),
        skip_existing_components=data.get('skip_existing_components', False),
        via_keepout_clearance_mm=data.get('via_keepout_clearance_mm', 0.2),
        via_search_step_mm=data.get('via_search_step_mm', 0.1),
        via_search_max_radius_mm=data.get('via_search_max_radius_mm', 3.0),
        via_search_n_directions=data.get('via_search_n_directions', 8),
        schematic_dir=schematic_dir,
        schematic_files=schematic_files,
        registry_path=registry_path,
        track_registry_path=track_registry_path,
        log_file=log_file,
    )
    total_spokes = sum(len(r.spokes) for r in cfg.rules)
    logger.debug(_("Config loaded: layer={layer}, cells={cells}, points={points}, rules={rules}, "
                   "spokes={spokes}, clone_placements={clones}").format(
                       layer=cfg.layer, cells=len(cfg.cells), points=len(cfg.points),
                       rules=len(cfg.rules), spokes=total_spokes, clones=len(cfg.clone_placements)))
    return cfg, ctx