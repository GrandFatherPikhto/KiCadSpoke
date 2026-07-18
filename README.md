# KiCadSpoke v1.0.10

**KiCadSpoke** is a command-line tool for automated placement of components and vias on PCBs in **KiCad** based on **templates** and **roles**. It connects to a running KiCad instance via IPC and performs:

- Moving components (capacitors, resistors, ferrites, crystals, etc.) to specified positions.
- Creating vias attached to the spoke as a whole or to individual components.
- **Cloning** repetitive functional blocks (PI-filters, DAC channels) at different locations on the board.
- Automatic component selection by **roles** and **nets** without specifying explicit refdes.
- Idempotency: repeated runs do not duplicate already correctly placed items.
- Undo of the last operation.
- Extracting templates from the current selection on the board.
- Snapshotting channels (for hierarchical projects) via a file-based cloner (no IPC).

---

## Key Features

- **Template-based approach** – geometry is described once in local coordinates and can be rotated/translated for each application.
- **Automatic component selection** – instead of refdes, roles are used (`LIGHT`, `HEAVY`, `PI_FILTER_C1`, etc.). Specific instances are taken from a pool by net and the `Role` field in the schematic.
- **Section cloning** (`ClonePlacement`) – supports two modes:
  - **by selection** – for one-off instances (e.g., a single MCU);
  - **by nets** – for repeatedly repeated blocks (with net name parametrization via placeholders and `params`).
- **Generalized vias** – vias can be at the spoke level and at the component level; all coordinates are relative to the spoke origin, making them independent of actual pad positions.
- **Placement registry** – stores UUIDs of created vias, ensuring idempotency and automatic cleanup of obsolete entries.
- **Pre-validation** – config checks before any board modifications (template/pad existence, component pool sufficiency, clone correctness).
- **Diagnostics** – a set of scripts for debugging IPC, geometry, and field reading.
- **File-based cloner** (`clone-extract`) – parses `.net` and `.kicad_pcb` without IPC, builds a twin map of channels for hierarchical projects.

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

### Spoke (ManualSpoke)
Attaches a template to a specific IC pin:
- `pad` – pad number of the target component.
- `shift_x_mm`, `shift_y_mm` – flat shift from the pad centre to the template origin.
- `rotation_deg` – rotation of the entire template.

### Roles and Component Pool
Instead of refdes, **roles** are used in the config. For each net (`rule.net`), a pool of components is built, where each component:
- Has a `Role` field with the required value.
- Has at least one pad connected to that net.

Components are sorted in natural numeric order (`C5` < `C10`) and consumed in the order of spokes.

### Cloning (ClonePlacement)
Allows applying a template at an arbitrary point on the board, without tying to IC pads. Supports:
- **Selection mode** – reads roles from the current selection in the PCB editor.
- **Net mode** – for each role, a net is specified (via `nets` or `net_template` with placeholders resolved by `params` and `net_overrides`).

### Placement Registry (PlacementRegistry)
Stores each via's UUID, position, parameters, and net in a JSON file next to the config. On subsequent runs:
- already correctly placed vias are skipped;
- those that changed position/parameters are deleted and recreated;
- obsolete entries (keys not present in the new plan) are removed (prune).

### Net Resolution (`net_resolution`)
For cloned templates, net names go through a three‑step resolution:
1. **Literal** – if no placeholders.
2. **Placeholder** – substitution from `params` (e.g., `{channel}` → `2`).
3. **net_overrides** – final override of the resolved name (for hierarchical paths).

---

## Configuration File Format (YAML)

### Root Parameters

| Field | Type | Description |
|-------|------|-------------|
| `target_ref` | string | Refdes of the target component (IC). |
| `side` | string | `"back"` or `"front"` (placement side). |
| `templates` | dict | Named spoke templates. |
| `rules` | list | Manual spoke rules. |
| `clone_placements` | list | Cloned placements (TemplatePlacer). |
| `thermal_via_array` | dict | Thermal via settings (optional). |
| `place_components` | boolean | Enable component moves (default `true`). |
| `skip_existing_components` | boolean | Skip components and vias already in place. |
| `via_keepout_clearance_mm` | number | Keepout clearance for thermal via search. |
| `via_search_step_mm` | number | Step size for free‑space search. |
| `via_search_max_radius_mm` | number | Maximum search radius. |
| `via_search_n_directions` | number | Number of search directions. |

### Template (`templates`)

```yaml
templates:
  cap_pair_standard:
    vias:
      - offset_along_mm: 0.0
        offset_across_mm: -1.5
        drill_mm: 0.3
        diameter_mm: 0.6
        # net omitted – for ManualSpoke it uses rule.net, for ClonePlacement it must be explicit
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

### Manual Spokes (`rules`)

```yaml
rules:
- net: +1V2_VCCINT
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

### Cloned Placements (`clone_placements`)

```yaml
clone_placements:
  - name: dac_channel_2
    template: dac_channel
    anchor_ref: IC1
    anchor_pad: '17'
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
    enabled: true
```

- If `anchor_ref` is set, `origin_x/y` become a **shift** from the anchor (not absolute coordinates).
- `anchor_pad` – optional, binds to a specific pad of the anchor.
- Without an anchor, `origin_x/y` define an absolute point.

### Thermal Vias (`thermal_via_array`)

```yaml
thermal_via_array:
  enabled: true
  target_ref: IC1
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

### `extract` – extract template from selection

```bash
python kicadspoke_cli.py extract --name template_name --output config.yaml [--verbose] [--log-file]
```

Before running, select the desired components and vias in the PCB editor. Roles must be unique.

### `undo` – undo the last operation

```bash
python kicadspoke_cli.py undo [--verbose] [--log-file]
```

Restores the board using the last operation log.

### `clone-extract` – snapshot a channel (file‑based cloner)

```bash
python kicadspoke_cli.py clone-extract --net project.net --pcb project.kicad_pcb --channel Channel_0 --output snapshot.yaml [--verbose]
```

Analyzes a hierarchical project, extracts all components, tracks, and vias belonging to the specified channel, and saves the snapshot in YAML. Useful for studying the channel structure before writing a clone config.

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

### 4. Extract a template
Select elements on the board, then:
```bash
python kicadspoke_cli.py extract --name my_filter --output my_config.yaml --verbose
```

### 5. Undo
```bash
python kicadspoke_cli.py undo --verbose
```

---

## Diagnostics and Known Issues

### KiCad Bug #24966 (crash on first write via IPC)
When the Schematic Editor is open and no interactive edit has been made in the session, calling `Board.update_items()` (even with no changes) can crash KiCad (null pointer in `API_HANDLER_EDITOR::checkForBusy`). The request is dispatched to the schematic handler, which is not initialized.

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
├── validation.py              # Pre‑validation checks
├── registry.py                # Via registry
├── net_resolution.py          # Net resolution for ClonePlacement
├── template_extraction.py     # Template extraction from selection
├── undo.py                    # Undo operation
├── geometry/                  # Geometry calculations (spoke_layout, keepout, thermal_grid, pad_projection, clone_geometry)
├── kicad/                     # KiCad IPC adapter
├── placement/                 # Planner, executors, services (collision, planner, executor, services)
├── cloner/                    # File‑based cloner (extract, netlist, pcb, models, sexp)
├── diagnostics/               # Diagnostic scripts
├── utils/                     # Utilities (units)
└── tests/                     # Unit and integration tests
```

---

## 📚 Technical Documentation

All technical documentation is located in the [`docs/`](./docs) folder and includes detailed descriptions of the architecture, modules, API, and usage scenarios. The complete list of documents is provided below.

| Document | Description |
|----------|-------------|
| **[architect.md](./docs/architect.md)** | **Project architecture.** Layers, key principles, module structure, component interactions, and design patterns. Essential for understanding the system. |
| **[commands.md](./docs/commands.md)** | **CLI commands.** Detailed description of all subcommands and flags of `kicadspoke_cli.py` (`apply`, `undo`, `extract`, `clone-extract`), with examples and parameter explanations. |
| **[diagnostics.md](./docs/diagnostics.md)** | **Diagnostic scripts.** Description of scripts in `kicadspoke/diagnostics/` and the external `hunt-proc.ps1` tool for IPC debugging and capturing KiCad crashes. |
| **[diagram.md](./docs/diagram.md)** | **Visual diagrams.** Contains sequence and module dependency diagrams (Mermaid format). Helps to quickly grasp the data flow. |
| **[geometry.md](./docs/geometry.md)** | **Geometry utilities.** Detailed description of `geometry/` modules (`keepout`, `spoke_layout`, `clone_geometry`, `thermal_grid`, `pad_projection`), their functions and relationships. |
| **[kicad.md](./docs/kicad.md)** | **KiCad adapter.** Documentation for `kicad/adapter.py` and `interfaces.py` – methods, IPC specifics, workaround for bug #24966, and use of unstable APIs. |
| **[kipy.md](./docs/kipy.md)** | **kicad‑python usage.** Complete list of all `kipy` API calls used, with stability status (stable, unstable, undocumented, deprecated) and links to official docs. |
| **[placement.md](./docs/placement.md)** | **Placement planning and execution.** Documentation for the `placement/` module – `Planner`, `Executor`, `services` (`ComponentPool`, `CloneRoleResolver`, calculators, `ViaPlanner`). |
| **[tests.md](./docs/tests.md)** | **Tests.** Structure of unit and integration tests, fixtures, run order, and coverage. |
| **[uplevel_modules.md](./docs/uplevel_modules.md)** | **Top‑level modules.** Description of root files: `config.py`, `validation.py`, `registry.py`, `net_resolution.py`, `template_extraction.py`, `undo.py`, `constants.py`, `exceptions.py`, and `kicadspoke_cli.py`. |
| **[cloner.md](./docs/cloner.md)** | **File‑based cloner.** Documentation for the `cloner/` module – parsing `.net` and `.kicad_pcb`, building the twin map, YAML snapshot format, and practical examples of using `clone-extract`. |

---

### 🔗 Additional Resources

- **[README.md](./README.md)** – project overview, installation, quick start, and config examples (this document).
- **[kicadspoke_cli.py](./kicadspoke_cli.py)** – CLI entry point with built‑in help (`python kicadspoke_cli.py --help`).
- **Example configurations** – ready‑to‑use YAML files in the project root:
  - [`10CL006YE144C8G.yaml`](./10CL006YE144C8G.yaml) – capacitor placement for an FPGA.
  - [`pi_filter_vccio.yaml`](./pi_filter_vccio.yaml) – PI‑filter cloning with net parametrization.
  - [`pi_filter_vccint.yaml`](./pi_filter_vccint.yaml) – another cloning example with anchor binding.
  - [`kicadspoke_templates_example.yaml`](./kicadspoke_templates_example.yaml) – demonstration of manual spokes and cloning.
- **Diagnostics** – scripts in [`kicadspoke/diagnostics/`](./kicadspoke/diagnostics/) for debugging and analysis.
- **KiCad bug reports**:
  - [`issue_24966_v2_description.md`](./issue_24966_v2_description.md) – detailed description of the crash on first write (use for reference if you encounter the crash).

---

### 🧭 How to Use the Documentation

1. **Start with `README.md`** – it contains the overview, installation, and basic examples.
2. For a deep understanding of the system, read **[architect.md](./docs/architect.md)** – it explains the rationale behind the architecture.
3. If you are writing a configuration, study **[commands.md](./docs/commands.md)** and the example YAML files.
4. For debugging and diagnostics, refer to **[diagnostics.md](./docs/diagnostics.md)** and the scripts in `diagnostics/`.
5. When working with section cloning, be sure to study **[cloner.md](./docs/cloner.md)** for preliminary channel analysis via `clone-extract`.
6. If you are modifying the code or adding new modules, review the corresponding document (e.g., **[placement.md](./docs/placement.md)** or **[geometry.md](./docs/geometry.md)**).

---

## License

This project is distributed under the **MIT** license. See the `LICENSE` file for details.