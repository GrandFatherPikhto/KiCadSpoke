# kicadspoke/config/models.py
"""
config/models.py — all configuration dataclasses (templates, ClonePlacement,
Rule, Config, etc.) WITHOUT any YAML loading/validation logic — this is purely
a description of the data shape. Loading is in config/loader.py.

Split from monolithic config.py by refactoring. The public interface of the
package remains unchanged — kicadspoke/config/__init__.py re‑exports everything
from here and from loader.py, so `from kicadspoke.config import Config, ClonePlacement, load_config`
continues to work exactly as before.
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from ..i18n import _


@dataclass
class ThermalViaArrayConfig:
    """Configuration for a thermal via array under an IC thermal pad.

    name — for --only (see kicadspoke_cli.py). REQUIRED in YAML if the
    thermal_via_array section is present at all (see config/loader.py — fatal,
    not a silent fallback). Here in the dataclass it's Optional only because
    tests/internal code that construct ThermalViaArrayConfig() directly in
    Python (bypassing the YAML loader) don't need a name — the requirement
    applies ONLY to human input via YAML, not to the data structure itself.
    """
    enabled: bool = False
    anchor_ref: Optional[str] = None
    anchor_role: Optional[str] = None
    anchor_sheet: Optional[str] = None
    anchor_cluster: Optional[str] = None
    pad: str = ""
    net: str = "GND"
    rows: int = 4
    cols: int = 4
    margin_mm: float = 0.5
    pattern: str = "grid"
    drill_mm: float = 0.3
    diameter_mm: float = 0.5
    name: Optional[str] = None


def thermal_via_array_effective_name(tva: "ThermalViaArrayConfig") -> Optional[str]:
    """Single point for reading the name for --only. Just tva.name — the loader
    guarantees it is set for any thermal_via_array that actually came from YAML;
    None only for manually constructed in tests."""
    return tva.name


@dataclass
class TemplateVia:
    """
    Via slot in a template — coordinates are ALWAYS along/across from the SPOKE
    origin (not from the component pad, even if the slot belongs to a specific
    component role) — same formula (local_to_absolute) as for the component
    position. net=None means "use the rule net" (rule.net).

    CHANGED (KiCadSpoke): previously power_via was the only field at the spoke
    level, while the GND via of a component was computed from the REAL pad of
    the already‑placed component (required reading the live board after commit).
    Now both concepts are the same slot — pure template geometry, independent
    of the live board. There can be any number of lists at both levels
    (spoke.vias and component.vias).
    """
    offset_along_mm: float = 0.0
    offset_across_mm: float = 0.0
    net: Optional[str] = None
    drill_mm: float = 0.3
    diameter_mm: float = 0.6


@dataclass
class TemplateComponentSlot:
    """
    One component slot in a template — a role ('HEAVY'/'LIGHT'/'XTAL'/
    'LOAD_CAP_1', etc.), not a specific ref. Roles MUST be unique within a
    single template (checked fatally during loading, see _load_spoke_template).
    The actual ref is selected during placement from the component pool:
    all footprints whose REAL pad sits on the rule net (rule.net) and whose
    custom Role field matches this role (see placement/services/component_pool.py).
    Coordinates are local (along/across) — from the SPOKE origin, not from the
    component itself. Vias in this slot use the same local system (see TemplateVia).

    net_template — OPTIONAL, for TemplatePlacer (role matching by nets, not by
    selection): the expected net of this component, with the same placeholder
    syntax as TemplateVia.net (see net_resolution.py). Not used at all for
    ManualSpoke/component_pool.py — there the role is looked up by (rule.net, Role)
    without any field here.
    """
    role: str
    offset_along_mm: float = 0.0
    offset_across_mm: float = 0.0
    angle_deg: float = 0.0
    vias: List[TemplateVia] = field(default_factory=list)
    net_template: Optional[str] = None
    # Layer of the slot — FACT, absolute: 'F.Cu' | 'B.Cu'. None = inherit from
    # template layer. Written by extract only for components that deviate from
    # the template layer.
    layer: Optional[str] = None


@dataclass
class TemplateTrack:
    """
    Straight copper track segment in the template — same local coordinate
    system (along/across from spoke origin) as TemplateVia. No association with
    roles/pads: like a via, a track has no user fields, so we trust geometry
    (all template elements are moved/rotated/mirrored by the same formula,
    see geometry/clone_geometry.py).

    A polyline is simply MULTIPLE TemplateTrack segments in sequence, joined
    end‑to‑end (exactly as kipy.board_types.Track stores them inside KiCad —
    there is no separate "polyline" entity). ArcTrack is deliberately not
    supported — not needed for PI‑filters; could be added later if needed.

    Collisions (whether the track crosses other copper/components in the new
    location) are NOT checked by this tool — deliberate decision (see chat
    discussion): we rely on KiCad DRC after placement, not on our own segment‑
    vs‑segment geometry checker.
    """
    start_along_mm: float = 0.0
    start_across_mm: float = 0.0
    end_along_mm: float = 0.0
    end_across_mm: float = 0.0
    width_mm: float = 0.25
    net: Optional[str] = None
    # Layer — same pattern as TemplateComponentSlot.layer: None = inherit from
    # template layer, when mirroring it is inverted by the same rule.
    layer: Optional[str] = None


@dataclass
class SpokeTemplate:
    """
    Spoke template — all geometry is local and rotation‑invariant: described
    once at rotation_deg=0 (the reference board orientation), then each actual
    spoke rotates it as a whole. Any list may be empty — e.g. a spoke with no
    vias, or a template with just one component.
    """
    name: str
    vias: List[TemplateVia] = field(default_factory=list)
    components: List[TemplateComponentSlot] = field(default_factory=list)
    tracks: List[TemplateTrack] = field(default_factory=list)
    # Template layer — FACT, absolute: 'F.Cu' | 'B.Cu', as extracted
    # (written automatically). Components without their own layer inherit it.
    # No automatic guesswork: the template is placed verbatim; to flip the whole
    # thing, use explicit mirror on the placement.
    layer: str = 'F.Cu'


@dataclass
class ManualSpoke:
    """
    A specific spoke on a specific FPGA pad. shift_x_mm/shift_y_mm and
    rotation_deg are ALWAYS in KiCad board coordinates (not local), tuned
    visually for the specific board. Order: first shift (shift_x, shift_y) from
    the pad centre to the spoke origin, then rotation of the resulting origin
    (and all template contents) by rotation_deg.

    IMPORTANT: no component refs here anymore — concrete components are
    automatically selected from the pool (see placement/services/component_pool.py)
    by matching the actual rule net (rule.net) and the custom Role field on the
    component, in the order of spokes in this list.
    """
    pad: str
    template: str
    shift_x_mm: float = 0.0
    shift_y_mm: float = 0.0
    rotation_deg: float = 0.0
    enabled: bool = True
    cluster: Optional[str] = None


@dataclass
class Rule:
    """Rule: a group of spokes around ONE anchor component, all on one net.
    anchor_ref OR anchor_role (mutually exclusive, exactly one required) —
    whose pads are listed in spokes. anchor_sheet/anchor_cluster narrow
    ambiguity of anchor_role, same principle as in ClonePlacement.

    name — OPTIONAL, for --only. Defaults to net when not set (see
    rule_effective_name). An explicit name is only needed to give a rule a
    more readable label than its net; it is NOT a grouping mechanism — do not
    reuse the same name across several rules to "bundle" them for --only, use
    a shared Cluster (anchor_cluster / spoke.cluster) for that instead. The
    loader fatals if two rules resolve to the same effective name (see
    config/loader.py) — add a distinguishing name: to one of them.

    enabled — whole‑rule switch (default True), same convention as
    ManualSpoke.enabled/ClonePlacement.enabled/ThermalViaArrayConfig.enabled.
    Always wins over --only/--cluster: a disabled rule is dropped before any
    CLI selection is applied, it cannot be resurrected by naming it explicitly
    on the command line — enabled: false means "does not exist on the board
    right now", not "excluded from this particular run".
    """
    net: str
    spokes: List[ManualSpoke]
    anchor_ref: Optional[str] = None
    anchor_role: Optional[str] = None
    anchor_sheet: Optional[str] = None
    anchor_cluster: Optional[str] = None
    name: Optional[str] = None
    enabled: bool = True


def rule_effective_name(rule: "Rule") -> str:
    """Single point for reading the identity used for --only: the explicit
    name if set, otherwise the net (net is guaranteed present on any Rule)."""
    return rule.name or rule.net


@dataclass
class ClonePlacement:
    """
    Applying a template at a new location (TemplatePlacer/Cloner) — unlike
    ManualSpoke (anchor = IC pad), the anchor here is just a name, not tied to
    any specific component (anchor_id in registry = f"name:{name}"). Two
    positioning modes:
      - anchor_ref set: origin = centre of anchor_pad (or footprint centre if
        anchor_pad omitted), origin_x_mm/origin_y_mm is an optional FLAT shift
        from the anchor (without rotation, like shift in ManualSpoke),
        rotation_deg rotates only the template contents.
      - anchor_ref not set: origin_x_mm/origin_y_mm is an ABSOLUTE point on
        the board (required).

    Role→ref mapping — EITHER via the current selection on the board (for rare,
    one‑off sections like a single MCU), OR via explicit nets
    (params/nets/net_overrides — for repeated sections like PI‑filters or DAC
    channels). Presence of params OR nets means "by nets" mode; absence means
    "by selection".

    template OR role (mutually exclusive, exactly one required):
      - template: as before, reference to a SpokeTemplate from cfg.templates.
      - role: for a ONE‑COMPONENT placement without a single via/track —
        creating a separate template file just for one role is cumbersome.
        ClonePositionCalculator synthesises a temporary SpokeTemplate "on the fly"
        (one component with that role at (0,0), angle 0) — templates: in YAML
        is not touched.
    """
    name: str
    origin_x_mm: float
    origin_y_mm: float
    rotation_deg: float = 0.0
    template: Optional[str] = None
    role: Optional[str] = None
    nets: Dict[str, str] = field(default_factory=dict)      # role -> net (literal)
    params: Dict[str, Any] = field(default_factory=dict)    # for {placeholder} in net templates
    net_overrides: Dict[str, str] = field(default_factory=dict)  # final override of resolved name
    enabled: bool = True
    anchor_ref: Optional[str] = None
    anchor_pad: Optional[str] = None
    # Alternative to anchor_ref — anchor by the Role field on the board, not by
    # refdes (survives re‑annotation). Mutually exclusive with anchor_ref (fatal
    # if both are set — see _load_clone_placement). anchor_sheet — ONLY narrows
    # ambiguity when there are 2+ candidates with the same anchor_role
    # (comparison by prefix of LOCAL hierarchical net name, e.g. '/Channel_0/...' —
    # NOT via sheet_path/UUID, which was empirically broken — see chat scripts).
    # Meaningless without anchor_role.
    anchor_role: Optional[str] = None
    anchor_sheet: Optional[str] = None
    # Cluster — second custom field (see constants.CLUSTER_FIELD_NAME),
    # physical instance/cluster, independent of anchor_ref/anchor_role.
    # Used in TWO places: (1) narrowing search for anchor_role (like anchor_sheet,
    # but via a different field), (2) narrowing ambiguous roles INSIDE the
    # template in resolve_roles_by_nets (replacing the dead _sheet_key step —
    # typical case: 4 identical C_IN_BULK on one sheet, but no sheet separator
    # because they share a common power rail). Comparison is by PREFIX segments
    # ('Channel_1' matches both 'Channel_1' and 'Channel_1/1V2_PLL_PI_FILTER'),
    # not by exact equality — hierarchy and flat names work with the same code.
    anchor_cluster: Optional[str] = None
    # Placement layer — FACT: None = template layer (place verbatim).
    # mirror — OPERATION, always manual: flip the whole construction
    # (geometry mirrored, angles 180°−φ, all layers inverted).
    # Contradiction between the two is fatal at load: mirror without layer change
    # or layer change without mirror is physically meaningless.
    layer: Optional[str] = None
    mirror: bool = False
    # Explicit override role -> ref (highest priority, bypassing net‑based search):
    # last resort when candidates are electrically indistinguishable
    # (e.g. three identical filters in one sheet).
    refs: Dict[str, str] = field(default_factory=dict)
    # Explicit request for selection mode — NOT inferred from absence of nets/params
    # (that implicit behaviour remains the default for backward compatibility,
    # see clone_uses_selection_mode). Needed separately from implicit because
    # params is ALSO used for resolving placeholders in via/track nets
    # (apply_clone_geometry calls resolve_net regardless of the role mode) —
    # without this flag, a params intended only for via net resolution would
    # silently switch the whole clone_placement to "by nets" mode, breaking roles
    # resolved by selection. by_selection: true + non‑empty nets is fatal at load
    # (contradiction: nets has no meaning in selection mode).
    by_selection: bool = False


@dataclass
class Config:
    """Main configuration object."""
    # Spoke layer (ManualSpoke path): 'F.Cu' | 'B.Cu'. clone_placements have
    # their own layer/mirror per placement; this field does not affect them.
    layer: str = 'F.Cu'
    templates: Dict[str, SpokeTemplate] = field(default_factory=dict)
    thermal_via_array: ThermalViaArrayConfig = field(default_factory=ThermalViaArrayConfig)
    rules: List[Rule] = field(default_factory=list)
    clone_placements: List[ClonePlacement] = field(default_factory=list)
    place_components: bool = True
    skip_existing_components: bool = False
    # Free‑space search parameters — currently used only for thermal vias
    # (power/GND vias are placed manually, no search).
    via_keepout_clearance_mm: float = 0.2
    via_search_step_mm: float = 0.1
    via_search_max_radius_mm: float = 3.0
    via_search_n_directions: int = 8
    # For anchor_sheet (see ClonePlacement) — dict {uuid: Sheetname}
    # built by directly parsing *.kicad_sch (sexpdata, same format as cloner),
    # NOT through kipy — see discussion: sheet_path.path_human_readable is broken
    # in this KiCad version, and UUID from kipy (path[:-1]) empirically matches
    # the sheet UUIDs in .kicad_sch. schematic_dir — folder containing all
    # *.kicad_sch of the project (path relative to the YAML config itself,
    # like templates_file); schematic_files — extra files for sheets outside
    # schematic_dir.
    schematic_dir: Optional[str] = None
    schematic_files: List[str] = field(default_factory=list)
    # Explicit override for registry file paths — by default they are derived
    # from the CONFIG file name itself (registry_path_for_config), which changes
    # when the config is renamed. Paths are relative to this YAML, like templates_file.
    registry_path: Optional[str] = None
    track_registry_path: Optional[str] = None
    # Path to log file for `apply` of this config (relative to this YAML,
    # like registry_path) — useful to avoid passing --log-file manually each time
    # for the same board profile. CLI flag --log-file, if given, TAKES PRIORITY
    # over this field (see main() in kicadspoke_cli.py).
    log_file: Optional[str] = None
    # Computed in load_config from schematic_dir/schematic_files — NOT read from
    # YAML directly. {uuid: Sheetname}, empty if neither schematic_dir nor
    # schematic_files are set (and anchor_sheet cannot be used then — fatal in
    # validation.py).
    sheet_names: Dict[str, str] = field(default_factory=dict)

    @property
    def anchor_refs(self) -> set:
        """All anchor refs in the config: spoke rules + thermal via array."""
        out = {r.anchor_ref for r in self.rules if r.anchor_ref}
        if self.thermal_via_array.enabled and self.thermal_via_array.anchor_ref:
            out.add(self.thermal_via_array.anchor_ref)
        return out