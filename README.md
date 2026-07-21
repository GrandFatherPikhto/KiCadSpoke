# KiCadSpoke v1.0.10

**KiCadSpoke** is a command-line tool for automated placement of components and vias on PCBs in **KiCad** based on **templates** and **roles**. It connects to a running KiCad instance via IPC and performs:

- Moving components (capacitors, resistors, ferrites, crystals, etc.) to specified positions.
- Creating vias attached to the spoke as a whole or to individual components.
- **Cloning** repetitive functional blocks (PI-filters, DAC channels) at different locations on the board.
- Automatic component selection by **roles** and **nets** without specifying explicit refdes.
- Idempotency: repeated runs do not duplicate already correctly placed items.
- Undo of the last operation.
- Extracting templates from the current selection on the board with net parametrisation and custom origin selection.
- Snapshotting channels (for hierarchical projects) via a file‑based cloner (`clone-extract`).
- **Template transformation** (rotation, mirroring, origin shift) using the separate script `transform_template.py`.

---

## Key Features

- **Template-based approach** – geometry is described once in local coordinates and can be rotated/translated for each application.
- **Automatic component selection** – instead of refdes, roles are used (`LIGHT`, `HEAVY`, `PI_FILTER_C1`, etc.). Specific instances are taken from a pool by net and the `Role` field in the schematic.
- **Section cloning** (`ClonePlacement`) – supports two modes:
  - **by selection** – for one‑off instances (e.g., a single MCU);
  - **by nets** – for repeatedly repeated blocks (with net name parametrisation via placeholders and `params`). In this mode, ambiguity is resolved by physical proximity to the anchor (if components are electrically indistinguishable but located close to different anchors).
- **Generalised vias** – vias can be at the spoke level and at the component level; all coordinates are relative to the spoke origin, making them independent of actual pad positions.
- **Placement registry** – stores UUIDs of created vias, ensuring idempotency and automatic cleanup of obsolete entries. Now reconciliation is performed against real vias on the board, not only the JSON record, to avoid desynchronisation.
- **Pre‑validation** – config checks before any board modifications (template/pad existence, component pool sufficiency, clone correctness, and resolved via nets existence on the board).
- **Diagnostics** – a set of scripts for debugging IPC, geometry, and field reading.
- **File‑based cloner** (`clone-extract`) – parses `.net` and `.kicad_pcb` without IPC, builds a twin map of channels for hierarchical projects.
- **Template transformation** – the script `utils/transform_template.py` allows rotating, mirroring, and shifting the origin of existing templates without re‑extracting from the board.
- **Enhanced `extract`** – when extracting a template, you can set the origin to a specific via or component (instead of the bbox) and parameterise nets (replace literals with patterns containing placeholders).

---

## Installation and Dependencies

### Requirements
- Python 3.8 or later.
- KiCad 8.0 or later (with IPC API enabled).
- The **kipy** library (Python wrapper for KiCad IPC).

### Installation
```bash
pip install kipy pyyaml sexpdata
```
(For diagnostics, `psutil` may be required.)

### Setting up Roles in the Schematic (Eeschema)
Before using the tool, you need to add a custom field `Role` to components that should participate in placement:
1. Open the symbol in Eeschema.
2. Add a field named **Role** with a value matching the role in the template (e.g., `LIGHT`, `HEAVY`).
3. Run **Update PCB from Schematic** to propagate the field to the board.
4. Verify field readability with the diagnostic script:
   ```bash
   python -m kicadspoke.diagnostics.test_custom_field C5 --field Role
   ```

---

## Key Concepts

### Template (SpokeTemplate)
A template describes the **local geometry** of one "spoke" – a set of components and vias relative to a local origin (0,0) in the `along/across` coordinate system. It contains:
- **`vias`** – vias at the spoke level (usually the power net).
- **`components`** – a list of slots, each with a `role`, local coordinates, angle, and a list of vias (usually to GND).

All coordinates are defined **once** at `rotation_deg=0`; when applied, the template is rotated as a whole.

#### Template layer (`layer`)
Each template now has an absolute layer (`F.Cu` or `B.Cu`), which is automatically set during extraction (`extract`). Components lying on a different layer receive an explicit `layer` in their slot.

#### Net parametrisation during extraction
When extracting a template, you can replace literal net names with patterns containing placeholders using the `--net-template` and `--param` options of the `extract` command. This eliminates manual YAML editing.

### Spoke (ManualSpoke)
Attaches a template to a specific IC pin:
- `pad` – pad number of the target component.
- `shift_x_mm`, `shift_y_mm` – flat shift from the pad centre to the template origin.
- `rotation_deg` – rotation of the entire template.

**Important:** In new config versions, each rule (`rules`) must have its own `anchor_ref` (anchor component). The global `target_ref` has been removed.

### Roles and Component Pool
Instead of refdes, **roles** are used in the config. For each net (`rule.net`), a pool of components is built, where each component:
- Has a `Role` field with the required value.
- Has at least one pad connected to that net.

Components are sorted in natural numeric order (`C5` < `C10`) and consumed in the order of spokes.

### Cloning (ClonePlacement)
Allows applying a template at an arbitrary point on the board, without tying to IC pads. Supports:
- **Selection mode** – reads roles from the current selection in the PCB editor.
- **Net mode** – for each role, a net is specified (via `nets` or `net_template` with placeholders resolved by `params` and `net_overrides`). If multiple candidates are electrically indistinguishable, the tool can pick the one closest to the anchor (if the distance margin is sufficient). This is useful for power filters on a common rail.

### Placement Registry (PlacementRegistry)
Stores each via's UUID, position, parameters, and net in a JSON file next to the config. On subsequent runs:
- already correctly placed vias are skipped;
- those that changed position/parameters are deleted and recreated;
- obsolete entries (keys not present in the new plan) are removed (prune).

**Important:** Reconciliation now checks against real vias on the board (`adapter.get_vias()`), not only the JSON record, preventing desynchronisation due to manual deletions or crashes between registry write and board commit.

### Net Resolution (`net_resolution`)
For cloned templates, net names go through a three‑step resolution:
1. **Literal** – if no placeholders.
2. **Placeholder** – substitution from `params` (e.g., `{channel}` → `2`).
3. **net_overrides** – final override of the resolved name (for hierarchical paths).

During extraction, the reverse operation (`--net-template`) is available, turning literals into patterns.

### Template Transformation (`transform_template.py`)
A separate script for post‑processing templates:
- **Rotation** by an arbitrary angle.
- **Mirroring** along the X or Y axis.
- **Origin shift** to a specified via (by index or net name) or component (by index or role), or explicit offset.

This allows quick adaptation of a template to different orientations without re‑extracting.

---

## Configuration File Format (YAML)

### Root Parameters (new)

| Field | Type | Description |
|-------|------|-------------|
| `layer` | string | Global layer for ManualSpoke rules: `"F.Cu"` or `"B.Cu"` (replaces deprecated `side`). |
| `templates` | dict | Named spoke templates. |
| `rules` | list | Manual spoke rules, each with `anchor_ref`. |
| `clone_placements` | list | Cloned placements (TemplatePlacer). |
| `thermal_via_array` | dict | Thermal via settings (optional). |
| `place_components` | boolean | Enable component moves (default `true`). |
| `skip_existing_components` | boolean | Skip components and vias already in place. |
| `via_keepout_clearance_mm`, `via_search_step_mm`, `via_search_max_radius_mm`, `via_search_n_directions` | numbers | Parameters for thermal via placement search. |

**Deprecated fields:** `target_ref` and `side` at the root are no longer supported (fatal error on load).

### Template (`templates`) – now with `layer`

```yaml
templates:
  cap_pair_standard:
    layer: B.Cu       # absolute layer of the template
    vias:
      - offset_along_mm: 0.0
        offset_across_mm: -1.5
        drill_mm: 0.3
        diameter_mm: 0.6
        # net omitted – for ManualSpoke uses rule.net, for ClonePlacement must be explicit
    components:
      - role: LIGHT
        offset_along_mm: 1.0
        offset_across_mm: -1.0
        angle_deg: 90.0
        vias:
          - offset_along_mm: 0.0
            offset_across_mm: -1.0
            net: GND
            drill_mm: 0.3
            diameter_mm: 0.6
      - role: HEAVY
        offset_along_mm: 1.0
        offset_across_mm: 2.0
        angle_deg: 270.0
        vias:
          - offset_along_mm: 0.0
            offset_across_mm: 1.3
            net: GND
            drill_mm: 0.3
            diameter_mm: 0.6
```

### Manual Spokes (`rules`) – now with `anchor_ref`

```yaml
rules:
- net: +1V2_VCCINT
  anchor_ref: IC1       # mandatory field – anchor for this rule
  spokes:
  - pad: '109'
    template: cap_pair_standard
    shift_x_mm: 0.0
    shift_y_mm: 0.0
    rotation_deg: 90.0
  - pad: '62'
    template: cap_pair_standard
    shift_x_mm: 0.4
    shift_y_mm: 0.0
    rotation_deg: 270.0
```

### Cloned Placements (`clone_placements`) – new fields

```yaml
clone_placements:
  - name: dac_channel_2
    template: dac_channel
    anchor_ref: IC1
    anchor_pad: '17'      # optional
    origin_x_mm: -10.0
    origin_y_mm: -10.0
    rotation_deg: 90.0
    params:
      channel: 2
    nets:
      CAP_IN: "GPIO12"
      CAP_OUT: "GPIO12_FILTERED"
    net_overrides:
      "/STM32F4xx/BOOT0": "/STM32F4xx_2/BOOT0"
    layer: B.Cu          # explicit placement layer (if different from template layer)
    mirror: true         # mirror the whole construction (requires layer change)
    refs:                # explicit role -> ref mapping (last resort)
      DAC_PI_FILTER_C1: C601
    enabled: true
```

- If `anchor_ref` is set, `origin_x/y` become a **shift** from the anchor.
- `layer` and `mirror` allow placing the template on the opposite side with mirroring.

### Thermal Vias (`thermal_via_array`) – `target_ref` renamed to `anchor_ref`

```yaml
thermal_via_array:
  enabled: true
  anchor_ref: IC1       # was target_ref
  pad: '145'
  net: GND
  rows: 4
  cols: 4
  margin_mm: 0.5
  pattern: grid
  drill_mm: 0.3
  diameter_mm: 0.5
```

---

## CLI Commands

All commands are run via `kicadspoke_cli.py`. If the subcommand is omitted, `apply` is assumed.

### `apply` – apply placement

```bash
python kicadspoke_cli.py apply config.yaml [options]
```

Options:
- `--dry-run` – only show the plan, do not apply changes.
- `--timeout-ms` – IPC timeout in ms (default 20000).
- `--batch-size` – batch size for commits (default 10).
- `--verbose` – verbose output (DEBUG).
- `--log-file` – save logs to a file.
- `--no-collision-check` – disable collision checking.
- `--collision-margin` – margin for collision checking in mm (default 0.2).
- `--clone-placement NAME` – process only the specified clone (useful for debugging).

### `extract` – extract template from selection (enhanced)

```bash
python kicadspoke_cli.py extract --name template_name --output config.yaml [--verbose] [--log-file] [--param KEY=VALUE] [--net-template LITERAL=PATTERN] [--origin-by-via-net NET] [--origin-by-component-role ROLE]
```

New options:
- `--param KEY=VALUE` – sets a parameter for verifying `--net-template` (e.g., `channel=1`). Not written to the template, only used for validation.
- `--net-template LITERAL=PATTERN` – replaces a real net with a pattern containing a placeholder (e.g., `DAC1_DB1=DAC{channel}_DB1`). Can be repeated.
- `--origin-by-via-net NET` – sets the template origin to the position of the via with the specified net (instead of the bbox lower-left corner). Fatal if no such via or more than one exists.
- `--origin-by-component-role ROLE` – sets the origin to the position of the component with the specified role.

Before running, select the desired components and vias in the PCB editor. Roles must be unique.

### `undo` – undo the last operation

```bash
python kicadspoke_cli.py undo [--verbose] [--log-file]
```

### `clone-extract` – snapshot a channel (file‑based cloner)

```bash
python kicadspoke_cli.py clone-extract --net project.net --pcb project.kicad_pcb --channel Channel_0 --output snapshot.yaml [--verbose]
```

Analyzes a hierarchical project, extracts all components, tracks, and vias belonging to the specified channel, and saves the snapshot in YAML. Useful for studying the channel structure before writing a clone config.

### `transform_template.py` – separate tool for template transformation

This script is located in `utils/` and is used for post‑processing existing YAML templates:

```bash
python utils/transform_template.py -i input.yaml -o output.yaml --rotate 90 --mirror-x --set-origin-by-via-net "GND"
```

Options:
- `--rotate DEG` – rotate counter‑clockwise by angle (degrees).
- `--mirror-x` – mirror along the X axis (flips `across` sign).
- `--mirror-y` – mirror along the Y axis (flips `along` sign).
- `--set-origin-by-via-index N` – shift origin to the via with index N.
- `--set-origin-by-via-net NET` – shift origin to the via with the given net.
- `--set-origin-by-component-index N` – shift origin to the component with index N.
- `--set-origin-by-component-role ROLE` – shift origin to the component with the given role.
- `--origin-x X --origin-y Y` – explicit origin offset.

Order of application: first origin shift (if set), then rotation and mirroring.

---

## Usage Examples

### 1. Standard run
```bash
python kicadspoke_cli.py 10CL006YE144C8G.yaml
```

### 2. Dry run (preview only)
```bash
python kicadspoke_cli.py config.yaml --dry-run
```

### 3. Process a single clone when multiple clones are in selection mode
```bash
python kicadspoke_cli.py config.yaml --clone-placement pi_filter_vccio
```

### 4. Extract a template with parametrisation and origin by via
Select elements on the board, then:
```bash
python kicadspoke_cli.py extract --name my_filter --output my_config.yaml --net-template "DAC1_DB1=DAC{channel}_DB1" --param channel=1 --origin-by-via-net "/Channel_0/DAC/+3V3_CLKVDD" --verbose
```

### 5. Transform a template
```bash
python utils/transform_template.py -i my_template.yaml -o my_template_rotated.yaml --rotate 180 --set-origin-by-component-role FB
```

### 6. Undo
```bash
python kicadspoke_cli.py undo --verbose
```

---

## Diagnostics and Known Issues

### KiCad Bug #24966 (crash on first write via IPC)
When the Schematic Editor is open and no interactive edit has been made in the session, calling `Board.update_items()` (even with no changes) can crash KiCad (null pointer in `API_HANDLER_EDITOR::checkForBusy`). The request is dispatched to the schematic handler, which is not initialised.

**Symptoms:** KiCad silently closes, the client receives `ConnectionError: Error receiving reply from KiCad: Timed out`.

**Workaround:** before running `apply`, either close the schematic editor or perform any interactive edit in PCB (move a component and save). The code includes a warning (`check_write_crash_risk`) and retries with backoff, but the crash remains possible – it's a KiCad defect.

### Diagnostic scripts
The folder `kicadspoke/diagnostics/` contains scripts for debugging:
- `diagnose_first_write_crash.py` – runs a ladder of reads/writes to localise the crash.
- `test_custom_fields.py` – checks reading of the `Role` field.
- `test_move_one_cap.py` – minimal test for moving a component.
- `test_flip_one_cap.py` – tests flip via GUI action.
- `test_create_one_via.py` – tests via creation.
- `test_pad_mirror_convention.py` – empirical check of pad mirroring on flip.
- `get_selected_component.py` – prints information about selected components.

---

## Project Structure (brief)

```
kicadspoke/
├── kicadspoke_cli.py          # CLI entry point
├── config.py                  # YAML loading → dataclasses
├── constants.py               # Global constants
├── exceptions.py              # Exception hierarchy
├── validation.py              # Pre‑validation checks (including via net existence)
├── registry.py                # Via registry (reconcile with live vias)
├── net_resolution.py          # Net resolution for ClonePlacement and reverse parametrisation
├── template_extraction.py     # Template extraction (with new options)
├── undo.py                    # Undo operation
├── geometry/                  # Geometry calculations (spoke_layout, keepout, thermal_grid, pad_projection, clone_geometry)
├── kicad/                     # KiCad IPC adapter
├── placement/                 # Planner, executors, services (collision, planner, executor, services)
│   ├── services/              # Including clone_role_resolver with anchor‑proximity disambiguation
├── cloner/                    # File‑based cloner (extract, netlist, pcb, models, sexp)
├── diagnostics/               # Diagnostic scripts
├── utils/                     # Utilities (units, transform_template.py)
└── tests/                     # Unit and integration tests
```

---

## 📚 Technical Documentation

Detailed descriptions of architecture, modules, and API are in the `docs/` folder:

- [Project architecture](./docs/architect.md)
- [CLI commands](./docs/commands.md)
- [Diagnostics](./docs/diagnostics.md)
- [Geometry utilities](./docs/geometry.md)
- [KiCad adapter](./docs/kicad.md)
- [Using kipy](./docs/kipy.md)
- [Placement planning and execution](./docs/placement.md)
- [Tests](./docs/tests.md)
- [Top‑level modules](./docs/uplevel_modules.md)
- [File‑based cloner](./docs/cloner.md)
- [Template transformation](./docs/rotate_template.md) (new)

---

## License

This project is distributed under the **MIT** license. See the `LICENSE` file for details.