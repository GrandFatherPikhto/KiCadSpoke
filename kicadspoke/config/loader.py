# kicadspoke/config/loader.py
"""
config/loader.py — all YAML loading/validation logic for dataclasses
from config/models.py: load_config() (entry point) and all _load_* functions.
Split from monolithic config.py by the same refactoring as models.py.
"""
import logging
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
import yaml

from ..exceptions import ValidationError, format_fatal_error, check_unknown_keys
from ..sheet_names import build_sheet_name_map
from .includes import resolve_includes
from .models import (
    ThermalViaArrayConfig, TemplateVia, TemplateComponentSlot, TemplateTrack,
    SpokeTemplate, ManualSpoke, Rule, ClonePlacement, Config, rule_effective_name,
)
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
               "(net_overrides is a sibling of template/params, not under via)")]
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
               "to inherit the template layer")]
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


def _load_spoke_template(name: str, data: Dict[str, Any]) -> SpokeTemplate:
    components = [_load_template_component_slot(c) for c in data.get('components', [])]

    roles = [c.role for c in components]
    duplicates = {r for r in roles if roles.count(r) > 1}
    if duplicates:
        raise ValidationError(format_fatal_error(
            _("role appears twice in template {name!r}").format(name=name),
            [_("role {role!r} appears {count} times in components of this template – "
               "roles inside a template must be unique (see anchor_id/template_name/role "
               "in the placement registry)").format(role=r, count=roles.count(r))
             for r in sorted(duplicates)]
        ))

    if 'reference_side' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'reference_side' in template {name!r}").format(name=name),
            [_("renamed (see discussion v116): use layer: F.Cu or layer: B.Cu – "
               "absolute template layer, as extracted")]
        ))
    layer = data.get('layer', 'F.Cu')
    _check_layer_value(layer, _("in template {name!r}").format(name=name))

    return SpokeTemplate(
        name=name,
        vias=[_load_template_via(v) for v in data.get('vias', [])],
        components=components,
        tracks=[_load_template_track(t) for t in data.get('tracks', [])],
        layer=layer,
    )


_MANUAL_SPOKE_KNOWN_KEYS = {
    'pad', 'template', 'shift_x_mm', 'shift_y_mm', 'rotation_deg',
    'enabled', 'cluster', 'active',
}


def _load_manual_spoke(data: Dict[str, Any], rule_label: str) -> ManualSpoke:
    check_unknown_keys(data, _MANUAL_SPOKE_KNOWN_KEYS,
                       _("unknown fields in spoke (pad {pad!r}) of rule (net {net!r})")
                       .format(pad=data.get('pad', '?'), net=rule_label))
    return ManualSpoke(
        pad=data['pad'],
        template=data['template'],
        shift_x_mm=data.get('shift_x_mm', 0.0),
        shift_y_mm=data.get('shift_y_mm', 0.0),
        rotation_deg=data.get('rotation_deg', 0.0),
        enabled=data.get('enabled', True),
        cluster=data.get('cluster'),
        active=data.get('active', True),
    )


_RULE_KNOWN_KEYS = {
    'net', 'spokes', 'anchor_ref', 'anchor_role', 'anchor_sheet',
    'anchor_cluster', 'name', 'enabled', 'active',
}


_THERMAL_VIA_ARRAY_KNOWN_KEYS = {
    'enabled', 'anchor_ref', 'anchor_role', 'anchor_sheet', 'anchor_cluster',
    'pad', 'net', 'rows', 'cols', 'margin_mm', 'pattern', 'drill_mm',
    'diameter_mm', 'name', 'active',
}


_CLONE_PLACEMENT_KNOWN_KEYS = {
    'name', 'template', 'role', 'origin_x_mm', 'origin_y_mm', 'rotation_deg',
    'nets', 'params', 'net_overrides', 'enabled', 'active',
    'anchor_ref', 'anchor_pad', 'anchor_role', 'anchor_sheet', 'anchor_cluster',
    'layer', 'mirror', 'refs', 'by_selection',
    'side',  # deprecated – recognised separately to give a migration message
}


def _load_clone_placement(data: Dict[str, Any]) -> ClonePlacement:
    name = data.get('name', '?')
    if not data.get('name'):
        raise ValidationError(format_fatal_error(
            _("clone_placement without name"),
            [_("every clone_placement must have a name – used in --only "
               "(kicadspoke_cli.py) for isolated runs; write name: <string>. "
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

    template = data.get('template')
    role = data.get('role')
    if template is not None and role is not None:
        raise ValidationError(format_fatal_error(
            _("template and role together in clone_placement {name!r}").format(name=name),
            [_("these are mutually exclusive ways to define the content: "
               "either a ready-made template (template), or a single-component placement "
               "by role (role), not both")]
        ))
    if template is None and role is None:
        raise ValidationError(format_fatal_error(
            _("neither template nor role set in clone_placement {name!r}").format(name=name),
            [_("need either template: <name from templates:>, or role: <ROLE> for "
               "a single-component placement without a separate template file")]
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

    if anchor_pad is not None and anchor_ref is None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            _("anchor_pad without anchor_ref/anchor_role in clone_placement {name!r}").format(name=name),
            [_("anchor_pad={pad!r} is set but no anchor specified – "
               "use anchor_ref: IC1 or anchor_role: SOME_ROLE").format(pad=anchor_pad)]
        ))

    has_anchor = anchor_ref is not None or anchor_role is not None

    if not has_anchor and ('origin_x_mm' not in data or 'origin_y_mm' not in data):
        raise ValidationError(format_fatal_error(
            _("no anchor and no absolute coordinates in clone_placement {name!r}").format(name=name),
            [_("either set origin_x_mm/origin_y_mm (absolute point on board), "
               "or anchor_ref/anchor_role (+ optionally anchor_pad) for anchor‑based placement")]
        ))

    if 'side' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'side' in clone_placement {name!r}").format(name=name),
            [_("side is now set by an explicit pair: layer: F.Cu|B.Cu (where we place – fact) "
               "+ mirror: true (how we place – operation, only meaningful when the layer changes "
               "relative to the template)")]
        ))
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
        template=template,
        role=role,
        origin_x_mm=data.get('origin_x_mm', 0.0),
        origin_y_mm=data.get('origin_y_mm', 0.0),
        rotation_deg=data.get('rotation_deg', 0.0),
        nets=nets,
        params=data.get('params', {}) or {},
        net_overrides=data.get('net_overrides', {}) or {},
        enabled=data.get('enabled', True),
        active=data.get('active', True),
        anchor_ref=anchor_ref,
        anchor_pad=str(anchor_pad) if anchor_pad is not None else None,
        anchor_role=anchor_role,
        anchor_sheet=anchor_sheet,
        anchor_cluster=anchor_cluster,
        layer=layer,
        mirror=bool(data.get('mirror', False)),
        refs=data.get('refs', {}) or {},
        by_selection=by_selection,
    )


def load_config(path: str) -> Config:
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
               "(kicadspoke_cli.py) for isolated runs; write name: <any understandable string>, "
               "e.g. name: fpga_thermal")]
        ))
    check_unknown_keys(tva_data, _THERMAL_VIA_ARRAY_KNOWN_KEYS,
                       _("unknown fields in thermal_via_array"))
    thermal_via = ThermalViaArrayConfig(
        enabled=tva_data.get('enabled', False),
        anchor_ref=tva_data.get('anchor_ref'),
        anchor_role=tva_data.get('anchor_role'),
        anchor_sheet=tva_data.get('anchor_sheet'),
        anchor_cluster=tva_data.get('anchor_cluster'),
        pad=tva_data.get('pad', ''),
        net=tva_data.get('net', 'GND'),
        rows=tva_data.get('rows', 4),
        cols=tva_data.get('cols', 4),
        margin_mm=tva_data.get('margin_mm', 0.5),
        pattern=tva_data.get('pattern', 'grid'),
        drill_mm=tva_data.get('drill_mm', 0.3),
        diameter_mm=tva_data.get('diameter_mm', 0.5),
        name=tva_data.get('name'),
        active=tva_data.get('active', True),
    )

    templates_data = dict(data.get('templates', {}) or {})

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
    # 'templates:' wrapper — unlike include:, which expects one). Merged
    # among THEMSELVES with fatal on a repeated name (independent files —
    # a collision is far more likely a copy-paste mistake than an
    # intentional override, same philosophy as include:'s _DICT_SECTIONS).
    # Inline templates: in this config file still overrides silently on top
    # of all of them, unchanged from templates_file's original behaviour.
    external_templates: Dict[str, Any] = {}
    for ext_file in external_files:
        templates_path = Path(path).parent / ext_file
        if not templates_path.exists():
            raise ValidationError(format_fatal_error(
                _("templates file {file!r} not found").format(file=ext_file),
                [_("expected at {path} (relative to the config file itself, "
                   "not the current working directory)").format(path=templates_path)]
            ))
        with open(templates_path, 'r', encoding='utf-8') as f:
            if templates_path.suffix.lower() == '.json':
                file_templates = json.load(f)
            else:
                file_templates = yaml.safe_load(f) or {}
        for name, tdata in (file_templates or {}).items():
            if name in external_templates:
                raise ValidationError(format_fatal_error(
                    _("duplicate template {name!r} across templates_file/template_files").format(name=name),
                    [_("defined in more than one external templates file (templates_file "
                       "and/or an entry of template_files) — external files are meant to "
                       "be independent, a repeated name is far more likely a mistake than "
                       "an intentional override; inline templates: in the config itself "
                       "CAN still override an external one, that is unaffected")]
                ))
            external_templates[name] = tdata

    merged = dict(external_templates)
    merged.update(templates_data)
    templates_data = merged
    if external_files:
        logger.info(_("Templates from {files}: {count_ext}, plus inline: {count_inline}")
                    .format(files=external_files, count_ext=len(external_templates),
                            count_inline=len(data.get('templates', {}) or {})))
    templates = {name: _load_spoke_template(name, tdata) for name, tdata in templates_data.items()}

    rules = []
    for rule_data in data.get('rules', []):
        rule_net = rule_data.get('net')
        check_unknown_keys(rule_data, _RULE_KNOWN_KEYS,
                           _("unknown fields in rule (net {net!r})").format(net=rule_net))
        anchor_ref = rule_data.get('anchor_ref')
        anchor_role = rule_data.get('anchor_role')
        anchor_sheet = rule_data.get('anchor_sheet')
        anchor_cluster = rule_data.get('anchor_cluster')

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
        if not anchor_ref and not anchor_role:
            raise ValidationError(format_fatal_error(
                _("rule (net {net!r}) without anchor_ref/anchor_role").format(net=rule_net),
                [_("a spoke rule must have an anchor – anchor_ref: <ref> (component whose "
                   "pads are listed in spokes), or anchor_role: <ROLE> (survives re‑annotation)")]
            ))
        spokes = [_load_manual_spoke(spoke_data, rule_net) for spoke_data in rule_data.get('spokes', [])]
        rules.append(Rule(net=rule_net, spokes=spokes, anchor_ref=anchor_ref,
                          anchor_role=anchor_role, anchor_sheet=anchor_sheet,
                          anchor_cluster=anchor_cluster, name=rule_data.get('name'),
                          enabled=rule_data.get('enabled', True),
                          active=rule_data.get('active', True)))

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
        tpl = templates.get(cp.template)
        if tpl is None:
            continue
        placement_layer = cp.layer if cp.layer is not None else tpl.layer
        layer_changed = placement_layer != tpl.layer
        if cp.mirror and not layer_changed:
            raise ValidationError(format_fatal_error(
                _("mirror without layer change in clone_placement {name!r}").format(name=cp.name),
                [_("template {tpl!r} is on {tpl_layer}, placement layer is {place_layer} – "
                   "mirror without changing side is physically meaningless: either set layer to "
                   "{opposite}, or remove mirror").format(
                       tpl=cp.template, tpl_layer=tpl.layer, place_layer=placement_layer,
                       opposite='B.Cu' if tpl.layer == 'F.Cu' else 'F.Cu')]
            ))
        if layer_changed and not cp.mirror:
            raise ValidationError(format_fatal_error(
                _("layer changed without mirror in clone_placement {name!r}").format(name=cp.name),
                [_("template {tpl!r} is on {tpl_layer}, placement layer is {place_layer} – "
                   "flipped footprints on non‑flipped sites are nonsense; add mirror: true, "
                   "or remove the layer override").format(
                       tpl=cp.template, tpl_layer=tpl.layer, place_layer=placement_layer)]
            ))

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

    cfg = Config(
        layer=root_layer,
        templates=templates,
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
        sheet_names=sheet_names,
        registry_path=registry_path,
        track_registry_path=track_registry_path,
        log_file=log_file,
    )
    total_spokes = sum(len(r.spokes) for r in cfg.rules)
    logger.debug(_("Config loaded: layer={layer}, templates={tpl}, rules={rules}, spokes={spokes}, "
                   "clone_placements={clones}").format(
                       layer=cfg.layer, tpl=len(cfg.templates), rules=len(cfg.rules),
                       spokes=total_spokes, clones=len(cfg.clone_placements)))
    return cfg