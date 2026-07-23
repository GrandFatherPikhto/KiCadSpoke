# KiCadSpoke v1.25.0

**KiCadSpoke** is a command‑line **PCB cloning and layout automation** tool for **KiCad 10**, designed as an advanced script‑based alternative to the traditional **KiCad Replicate Layout** plugin. It enables automated **block replication**, component placement, and routing of complex multi‑channel designs using **templates**, **roles**, and the IPC API.

- Moving components (capacitors, resistors, ferrites, crystals, etc.) to specified positions.
- Creating vias and **tracks** attached to the spoke as a whole or to individual components.
- **Cloning** repetitive functional blocks (PI‑filters, DAC channels, power supplies) at different board locations.
- Automatic component selection by **roles** and **nets** – no explicit refdes needed.
- Idempotency: repeated runs never duplicate already‑correctly‑placed items.
- Undo of the last operation.
- Extracting templates from the current selection with net parametrisation and custom origin selection.
- Snapshotting hierarchical channels via a file‑based cloner (`clone-extract`).

---

## Key Features

**An advanced, script‑driven alternative to the classic KiCad Replicate Layout plugin**, built for multi‑channel projects and automated design reuse via the KiCad IPC API.

- **Template‑based approach** – geometry is defined once in local coordinates and reused with arbitrary rotation/translation.
- **Automatic component selection** – roles (`LIGHT`, `HEAVY`, `PI_FILTER_C1`, etc.) replace refdes; components are picked from a pool by net and the `Role` field in the schematic.
- **ClonePlacement** – supports two modes:
  - **by selection** – for one‑off instances (e.g., a single MCU);
  - **by nets** – for repeated blocks, with net name parametrisation via placeholders and `params`. Ambiguity is resolved by physical proximity to the anchor (useful for power filters on common rails). You can also use explicit `refs` as a last resort.
- **Generalised vias and tracks** – all elements (vias, tracks, components) are defined in local coordinates and transformed uniformly (translation, rotation, mirroring).
- **Placement registry** – stores UUIDs of created vias and tracks, ensuring idempotency and automatic cleanup of obsolete entries. Reconciliation is now performed against real elements on the board (via `adapter.get_vias()`/`adapter.get_tracks()`), not only the JSON record, avoiding desynchronisation.
- **Pre‑validation** – checks config before any board modification:
  - existence of templates, pads, and anchor components;
  - component pool sufficiency by roles;
  - uniqueness of clone names and physical anchors (`anchor_ref`, `anchor_role`);
  - correctness of resolved via nets against real board nets;
  - validity of `layer`/`mirror` combinations (mirroring only when layer changes);
  - at most one selection‑based `clone_placement` per run (KiCad allows only one active selection).
- **Diagnostics** – scripts for debugging IPC, geometry, and field reading.
- **File‑based cloner** (`clone-extract`) – parses `.net` and `.kicad_pcb` without IPC, builds a twin map of channels for hierarchical projects.
- **Tracks in templates** – since v1.22.0, templates can include straight track segments (polylines are supported as a sequence of segments). Track collisions are not automatically checked (rely on KiCad DRC).
- **External template files** – templates can be stored separately as JSON or YAML and referenced via `templates_file:` in the main config, keeping the main file clean and diff‑friendly.

---

## Installation and Dependencies

### Requirements
- Python 3.8 or later.
- KiCad 10.0.4 or later (with IPC API enabled).
- The **kipy** library (Python wrapper for KiCad IPC).

### Installation
```bash
pip install kipy pyyaml sexpdata
```
(For diagnostics, `psutil` may be required.)

### Setting up Roles in the Schematic (Eeschema)
1. Open the symbol in Eeschema.
2. Add a field named **Role** with a value matching the role in the template (e.g., `LIGHT`, `HEAVY`).
3. Run **Update PCB from Schematic** to propagate the field to the board.
4. Verify readability with:
   ```bash
   python -m kicadspoke.diagnostics.test_custom_field C5 --field Role
   ```

---

## Key Concepts

### Template (SpokeTemplate)
A template describes the **local geometry** of one "spoke" – a set of components, vias, and tracks relative to a local origin (0,0) in the `along/across` coordinate system. It contains:
- **`vias`** – vias at the spoke level (usually the power net).
- **`components`** – a list of slots, each with a `role`, local coordinates, angle, and a list of vias (usually to GND).
- **`tracks`** – straight track segments (layer, width, net).

All coordinates are defined **once** at `rotation_deg=0`; when applied, the template is rotated as a whole.

#### Template layer (`layer`)
Each template has an absolute layer (`F.Cu` or `B.Cu`), automatically set during extraction (`extract`). Components on a different layer get an explicit `layer` in their slot.

#### Net parametrisation during extraction
With the `--net-template` option, you can replace literal net names with patterns containing placeholders (e.g., `DAC1_DB1 → DAC{channel}_DB1`) at extraction time. This eliminates manual YAML editing.

### Spoke (ManualSpoke)
Attaches a template to a specific IC pin:
- `pad` – pad number of the target component.
- `shift_x_mm`, `shift_y_mm` – flat shift from the pad centre to the template origin.
- `rotation_deg` – rotation of the entire template.

**Important:** In new config versions, each rule (`rules`) must have its own `anchor_ref`. The global `target_ref` has been removed.

### Roles and Component Pool
Instead of refdes, **roles** are used in the config. For each net (`rule.net`), a pool of components is built, where each component:
- Has a `Role` field with the required value.
- Has at least one pad connected to that net.

Components are sorted in natural numeric order (`C5` < `C10`) and consumed in the order of spokes.

### Cloning (ClonePlacement)
Allows applying a template at an arbitrary point on the board, without tying to IC pads. Supports:
- **Selection mode** – reads roles from the current selection in the PCB editor. You can either omit `nets`/`params` or explicitly set `by_selection: true`. Only one such clone can be processed per run (due to KiCad's single‑selection limitation).
- **Net mode** – for each role, a net is specified (via `nets` or `net_template` with placeholders resolved by `params` and `net_overrides`). If multiple candidates are electrically indistinguishable, the tool can pick the one closest to the anchor (if the distance margin is sufficient). This is useful for power filters on a common rail.
- **Anchor by role** (`anchor_role`) – an alternative to `anchor_ref`: instead of a refdes, you can specify the `Role` field of the anchor component. This survives re‑annotation. You can further narrow the search with `anchor_sheet` (local net prefix) or `anchor_pad`.
- **Explicit refs** (`refs`) – a last‑resort override when candidates are indistinguishable by nets, selection, or proximity.

### Placement Registry (PlacementRegistry + TrackRegistry)
Separate registries for vias and tracks (JSON files next to the config). On subsequent runs:
- already correctly placed items are skipped;
- those that changed position/parameters are deleted and recreated;
- obsolete entries (keys not present in the new plan) are removed (prune).

**Important:** Reconciliation now checks against real elements on the board (`adapter.get_vias()`/`adapter.get_tracks()`), not only the JSON record, preventing desynchronisation due to manual deletions or crashes between registry write and board commit.

### Net Resolution (`net_resolution`)
For cloned templates, net names go through a three‑step resolution:
1. **Literal** – if no placeholders.
2. **Placeholder** – substitution from `params` (e.g., `{channel}` → `2`).
3. **net_overrides** – final override of the resolved name (for hierarchical paths).

During extraction, the reverse operation (`--net-template`) is available, turning literals into patterns.

---

## Configuration File Format (YAML)

### Root Parameters

| Field | Type | Description |
|-------|------|-------------|
| `layer` | string | Global layer for ManualSpoke rules: `"F.Cu"` or `"B.Cu"` (replaces deprecated `side`). |
| `templates` | dict | Inline named spoke templates (optional). |
| `templates_file` | string | Path to an external JSON/YAML file containing templates (overridden by inline `templates`). |
| `rules` | list | Manual spoke rules, each with `anchor_ref`. |
| `clone_placements` | list | Cloned placements (TemplatePlacer). |
| `thermal_via_array` | dict | Thermal via settings (optional). |
| `place_components` | boolean | Enable component moves (default `true`). |
| `skip_existing_components` | boolean | Skip components and vias/tracks already in place. |
| `via_keepout_clearance_mm`, `via_search_step_mm`, `via_search_max_radius_mm`, `via_search_n_directions` | numbers | Parameters for thermal via placement search. |

**Deprecated:** `target_ref` and `side` at the root are no longer supported (fatal error on load).

### Template (`templates`) – now with `layer` and `tracks`

```yaml
templates:
  cap_pair_standard:
    layer: B.Cu
    vias:
      - offset_along_mm: 0.0
        offset_across_mm: -1.5
        drill_mm: 0.3
        diameter_mm: 0.6
        # net omitted – for ManualSpoke uses rule.net, for ClonePlacement must be explicit
    tracks:
      - start_along_mm: 0.0
        start_across_mm: 0.0
        end_along_mm: 1.0
        end_across_mm: 1.0
        width_mm: 0.25
        net: GND
        layer: F.Cu    # optional, inherits from template
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

### Manual Spokes (`rules`) – with `anchor_ref`

```yaml
rules:
- net: +1V2_VCCINT
  anchor_ref: IC1       # mandatory – anchor for this rule
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

### Cloned Placements (`clone_placements`) – all new fields

```yaml
clone_placements:
  - name: dac_channel_2
    template: dac_channel
    anchor_ref: IC1
    anchor_pad: '17'      # optional
    origin_x_mm: -10.0    # shift from anchor (if anchor_ref given) or absolute point
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
    by_selection: false  # explicit selection‑mode request (overrides implicit detection)
    refs:                # explicit role->ref mapping (last resort)
      DAC_PI_FILTER_C1: C601
    enabled: true
```

- If `anchor_ref` (or `anchor_role`) is set, `origin_x/y` become a **shift** from the anchor.
- `layer` and `mirror` allow placing the template on the opposite side with mirroring.
- `by_selection: true` forces selection mode even if `params` are present (which may be used for via resolution, not for role mapping).

### Thermal Vias (`thermal_via_array`) – renamed `target_ref` to `anchor_ref`

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
- `--collision-margin` – margin in mm (default 0.2).
- `--clone-placement NAME` – process only the specified clone (useful for debugging).

### `extract` – extract template from selection (enhanced)

```bash
python kicadspoke_cli.py extract --name template_name --output config.yaml [--verbose] [--log-file] [--param KEY=VALUE] [--net-template LITERAL=PATTERN] [--origin-by-via-net NET] [--origin-by-component-role ROLE]
```

New options:
- `--param KEY=VALUE` – parameter for `--net-template` verification (e.g., `channel=1`), not written to template.
- `--net-template LITERAL=PATTERN` – replace a real net with a pattern containing placeholders (e.g., `DAC1_DB1=DAC{channel}_DB1`). Can be repeated.
- `--origin-by-via-net NET` – set origin to the position of a via on the specified net (instead of bbox). Fatal if the net is missing or ambiguous.
- `--origin-by-component-role ROLE` – set origin to the position of a component with the specified role.

**Important:** The `--output` extension determines format: `.json` → JSON (plain dictionary), otherwise YAML. The file is written **without a `templates:` wrapper**, making it easy to use as a `templates_file`.

### `undo` – undo the last operation

```bash
python kicadspoke_cli.py undo [--verbose] [--log-file]
```

### `clone-extract` – snapshot a channel (file‑based cloner)

```bash
python kicadspoke_cli.py clone-extract --net project.net --pcb project.kicad_pcb --channel Channel_0 --output snapshot.yaml [--verbose]
```

---

## Usage Examples

### 1. Standard run
```bash
python kicadspoke_cli.py 10CL006YE144C8G.yaml
```

### 2. Dry run
```bash
python kicadspoke_cli.py config.yaml --dry-run
```

### 3. Process a single clone (selection mode)
```bash
python kicadspoke_cli.py config.yaml --clone-placement pi_filter_vccio
```

### 4. Extract a template with parametrisation and origin by via
Select the elements on the board, then:
```bash
python kicadspoke_cli.py extract --name my_filter --output my_filter.json --net-template "DAC1_DB1=DAC{channel}_DB1" --param channel=1 --origin-by-via-net "/Channel_0/DAC/+3V3_CLKVDD" --verbose
```

### 5. Undo
```bash
python kicadspoke_cli.py undo --verbose
```

---

## Diagnostics and Known Issues

### KiCad Bug #24966 (crash on first write via IPC)
When the Schematic Editor is open and no interactive edit has been made in the session, calling `Board.update_items()` (even with no changes) can crash KiCad (null pointer in `API_HANDLER_EDITOR::checkForBusy`).

**Symptoms:** KiCad silently closes, client gets `ConnectionError: Error receiving reply from KiCad: Timed out`.

**Workaround:** close the schematic editor or perform any interactive edit in PCB (move a component and save) before running `apply`. The tool includes a warning and retries, but the crash remains a KiCad defect.

### Diagnostic scripts
`kicadspoke/diagnostics/` includes:
- `diagnose_first_write_crash.py` – reproduces the crash ladder.
- `test_custom_fields.py` – checks `Role` field reading.
- `test_move_one_cap.py`, `test_flip_one_cap.py`, `test_create_one_via.py`, `test_pad_mirror_convention.py`, `get_selected_component.py`, `get_pad_bbox.py`, `diagnostic_keepout.py`.

---

## Project Structure (brief)

```
kicadspoke/
├── kicadspoke_cli.py          # CLI entry point
├── config.py                  # YAML loading with templates_file support
├── constants.py
├── exceptions.py
├── validation.py              # Pre‑checks (nets, uniqueness, selection mode, layer/mirror)
├── registry.py                # Via and track registries (reconcile with live elements)
├── net_resolution.py
├── template_extraction.py     # Extract with parametrisation and custom origin
├── undo.py
├── geometry/                  # Spoke_layout, keepout, thermal_grid, pad_projection, clone_geometry
├── kicad/                     # KiCad IPC adapter
├── placement/                 # Planner, executors, services (collision, planner, executor, services)
│   ├── services/              # Including clone_role_resolver with proximity‑based disambiguation
├── cloner/                    # File‑based cloner (extract, netlist, pcb, models, sexp)
├── diagnostics/               # Diagnostic scripts
└── tests/                     # Unit and integration tests
```

---

## 📚 Technical Documentation

Detailed documentation is in the `docs/` folder:

- [Project architecture](./docs/architect.md)
- [CLI commands](./docs/commands.md)
- [Geometry utilities](./docs/geometry.md)
- [KiCad adapter](./docs/kicad.md)
- [Using kipy](./docs/kipy.md)
- [Placement planning and execution](./docs/placement.md)
- [Tests](./docs/tests.md)
- [Top‑level modules](./docs/uplevel_modules.md)
- [File‑based cloner](./docs/cloner.md)
- [Diagnostics](./docs/diagnostics.md)

> **Note:** some of these files may be missing if they haven't been created yet. The actual list is always available in the `docs/` folder.

---

## License

This project is distributed under the **MIT** license. See the `LICENSE` file for details.

---

**KiCadSpoke** is not just a utility – it's a modern alternative to manual block copying in KiCad.
