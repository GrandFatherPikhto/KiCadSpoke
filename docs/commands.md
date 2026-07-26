# KiCadSpoke Commands (CLI)

This document provides a complete reference for `kicadspoke_cli.py` commands and flags, config generators from `utils/`, and practical examples for typical scenarios. Verified against the code in the `main` branch (the project does not maintain version numbers/tags; refer to the commit date).

---

## Basic Syntax

```bash
python kicadspoke_cli.py <command> [options]
```

If no command is given, `apply` is assumed.

---

## `apply` – apply placement

Loads the configuration, connects to KiCad, performs validation, planning, and **three‑phase execution** (moves → vias → tracks).

### Syntax

```bash
python kicadspoke_cli.py apply <config.yaml> [options]
```

### Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Only print the plan (moves, vias, tracks), do not apply changes. |
| `--timeout-ms` | IPC timeout in milliseconds (default: `20000`). |
| `--batch-size` | Number of objects per transaction (default: `10`). |
| `--verbose` | Enable verbose output (DEBUG). |
| `--log-file` | Save logs to the specified file. |
| `--no-collision-check` | Disable collision checking (if false positives occur). |
| `--collision-margin` | Extra clearance for collision checking in mm (default: `0.2`). |
| `--clone-placement NAME` | Process only the specified clone by name. Useful when multiple clones are in selection mode (only one selection can be active in KiCad at a time) or for debugging a specific placement. |

**`log_file:` in the config itself** – an optional root‑level YAML field (like `templates_file`/
`registry_path`), resolved relative to the config file itself. If set, you don't need to pass
`--log-file` by hand every time for the same board profile. The CLI flag `--log-file`, if given, takes
priority over this field:
```yaml
log_file: ../logs/placer.log
```

**About the current production config:** the master config for the `3CH-AWG-TIA` board is `profiles/3ch-awg-tia.yaml` (merged `rules:`, `clone_placements:`, `thermal_via_array`, with a reference to `profiles/templates/3ch-awg-tia.yaml` via `templates_file`). The file `profiles/generated/10CL006YE144C8G.yaml` written by `utils/generate_10cl006.py` is a self‑contained archival version (can be run separately, but is no longer used in `apply` for this board).

### Examples

#### Standard run (place components, vias, and tracks)

```bash
python kicadspoke_cli.py apply profiles/3ch-awg-tia.yaml
```

#### Run with verbose logging to a file

```bash
python kicadspoke_cli.py apply 10CL006YE144C8G.yaml --verbose --log-file logs/placer.log
```

#### Preview (dry‑run) – does not modify the board

```bash
python kicadspoke_cli.py apply 10CL006YE144C8G.yaml --dry-run
```

#### Process only one clone (e.g., for debugging)

```bash
python kicadspoke_cli.py apply templates\pi_filter_vccio.yaml --clone-placement pi_filter_vccio
```

#### Disable collision checking

```bash
python kicadspoke_cli.py apply 10CL006YE144C8G.yaml --no-collision-check
```

#### Increase timeout for slow KiCad sessions

```bash
python kicadspoke_cli.py apply 10CL006YE144C8G.yaml --timeout-ms 30000
```

---

## `undo` – undo the last operation

Finds the most recent JSON log in the `logs/` folder and restores the board (moves components back to their original positions and layers, removes created vias **and tracks**).

### Syntax

```bash
python kicadspoke_cli.py undo [--verbose] [--log-file]
```

### Example

```bash
python kicadspoke_cli.py undo --verbose
```

---

## `extract` – extract a template from the current selection

Creates a spoke template from the current selection in the PCB editor. Each selected component must have a unique `Role` field. Supports extraction of **tracks** together with components and vias.

### Syntax

```bash
python kicadspoke_cli.py extract --name <template_name> --output <file> [--timeout-ms] [--verbose] [--log-file] [--param KEY=VALUE] [--net-template LITERAL=PATTERN] [--origin-by-via-net NET] [--origin-by-component-role ROLE] [--origin-by-component-pad PAD] [--profiles FILE] [--profile NAME]
```

### Options

| Flag | Description |
|------|-------------|
| `--name` | Name of the template (key in the `templates` section). |
| `--output` | Output file path. The extension determines the format: `.json` → JSON (flat dictionary), otherwise YAML. |
| `--timeout-ms` | IPC timeout in milliseconds (default: `20000`). |
| `--verbose` | Enable verbose output. |
| `--log-file` | Save logs to a file. |
| `--param KEY=VALUE` | Sets a parameter for verifying `--net-template` (e.g., `channel=1`). Not written to the template, only used for round‑trip validation. Can be repeated. |
| `--net-template LITERAL=PATTERN` | Replaces a real net name with a pattern containing placeholders (e.g., `DAC1_DB1=DAC{channel}_DB1`). Can be repeated. |
| `--origin-by-via-net NET` | Sets the template origin to the position of the via with the specified net (instead of the bbox lower‑left corner). Fatal if no such via exists or if there is more than one. Mutually exclusive with `--origin-by-component-role` (you can specify only one origin method). |
| `--origin-by-component-role ROLE` | Sets the origin to the position of the component with the specified role. Mutually exclusive with `--origin-by-via-net`. |
| `--origin-by-component-pad PAD` | Refines `--origin-by-component-role`: origin is the position of the specific pad of that component, not its centre. Without `--origin-by-component-role` it is fatal (you can only specify a pad for an already specified role). |
| `--profiles FILE` | YAML file with named profiles for `extract`. |
| `--profile NAME` | Use a profile from the `--profiles` file instead of explicit flags (cannot be combined with `--name`, `--output`, `--param`, `--net-template`, `--origin-by-*` – either everything from the profile or all explicit flags). |

**Important:** Before running, select the desired components, vias, and tracks in the PCB editor. Roles must be unique. When saving as JSON, the file is written **without a `templates:` wrapper**, making it directly usable as a `templates_file` in the main configuration.

### Examples

#### Extract a template to JSON with net parametrisation and origin by via

```bash
python kicadspoke_cli.py extract --name pi_filter_4 --output templates/pi_filter_4.json \
  --origin-by-via-net '+3V3_VCCIO' \
  --param PWR_IN='+3V3' --param PWR_OUT='+3V3_VCCIO' \
  --net-template '+3V3_VCCIO={PWR_OUT}' --net-template '+3V3={PWR_IN}' \
  --verbose
```

#### Extract using a profile

In `extract_profiles.yaml`:
```yaml
extract_profiles:
  my_filter:
    name: my_filter
    output: templates/my_filter.json
    param:
      PWR_IN: '+3V3'
      PWR_OUT: '+3V3_VCCIO'
    net_template:
      '+3V3_VCCIO': '{PWR_OUT}'
      '+3V3': '{PWR_IN}'
    origin_by_via_net: '+3V3_VCCIO'
```

Run:
```bash
python kicadspoke_cli.py extract --profiles extract_profiles.yaml --profile my_filter --verbose
```

#### Extract a template to YAML (no parametrisation)

```bash
python kicadspoke_cli.py extract --name my_filter --output my_filter.yaml --verbose
```

#### Add a template to an existing config (YAML)

```bash
python kicadspoke_cli.py extract --name my_filter --output 10CL006YE144C8G.yaml --verbose
```

Note: if a template with the same name already exists, it will be overwritten.

---

## `clone-extract` – snapshot a channel (file‑based cloner)

Analyzes a hierarchical project (without IPC) and extracts all components, tracks, and vias belonging to the specified channel, saving the snapshot as YAML. Useful for studying the channel structure before writing a ClonePlacement configuration.

### Syntax

```bash
python kicadspoke_cli.py clone-extract --net <file.net> --pcb <file.kicad_pcb> --channel <channel_name> --output <file.yaml> [--profiles FILE] [--profile NAME] [--verbose]
```

### Options

| Flag | Description |
|------|-------------|
| `--net` | Path to the `.net` file (netlist). |
| `--pcb` | Path to the `.kicad_pcb` file. |
| `--channel` | Channel name (e.g., `Channel_0`). |
| `--output` | Output YAML file. |
| `--profiles FILE` | YAML file with named profiles for `clone-extract`. |
| `--profile NAME` | Use a profile from the `--profiles` file instead of explicit flags. |
| `--verbose` | Enable verbose output. |

### Example

```bash
python kicadspoke_cli.py clone-extract --net my_project.net --pcb my_project.kicad_pcb --channel Channel_0 --output snapshot.yaml --verbose
```

Using a profile (`clone_profiles.yaml`):
```yaml
clone_profiles:
  channel0:
    net: my_project.net
    pcb: my_project.kicad_pcb
    channel: Channel_0
    output: snapshot.yaml
```

Run:
```bash
python kicadspoke_cli.py clone-extract --profiles clone_profiles.yaml --profile channel0 --verbose
```

The resulting YAML file contains a complete overview of the channel, which can be used to create a template and ClonePlacement entries.

---

## Utility scripts (`utils/`)

### `transform_template.py` – template transformation utility (optional)

A separate script for post‑processing existing templates (YAML or JSON). It allows rotating, mirroring, and shifting the origin without re‑extracting from the board.

#### Syntax

```bash
python utils/transform_template.py -i <input_file> -o <output_file> [options]
```

#### Options

| Flag | Description |
|------|-------------|
| `-i, --input` | Input YAML/JSON template file. |
| `-o, --output` | Output file (format determined by extension). |
| `--rotate DEG` | Rotate counter‑clockwise by angle (degrees). |
| `--mirror-x` | Mirror along X axis (flips `across` sign). |
| `--mirror-y` | Mirror along Y axis (flips `along` sign). |
| `--set-origin-by-via-index N` | Shift origin to the via at index N (0‑based). |
| `--set-origin-by-via-net NET` | Shift origin to the via with the given net. |
| `--set-origin-by-component-index N` | Shift origin to the component at index N. |
| `--set-origin-by-component-role ROLE` | Shift origin to the component with the given role. |
| `--origin-x X --origin-y Y` | Explicit origin offset in mm. |

**Order of application:** first origin shift (if specified), then rotation and mirroring. This ensures that the target element ends up at (0,0) after all transformations.

**Known limitation:** the script transforms only `vias` and `components`. The `tracks` section (if present – e.g., in `cap_pair_standard` / `cap_pair_standard_clone` in `profiles/templates/3ch-awg-tia.yaml`) **is not read or propagated** to the output – when transforming a template with tracks, they are silently lost in the output file. For templates containing tracks, do not use this script, or manually add the `tracks` section to the output file.

#### Examples

#### Rotate 180° and shift origin to the via with net "GND"

```bash
python utils/transform_template.py -i template.yaml -o template_rotated.yaml --rotate 180 --set-origin-by-via-net "GND"
```

#### Mirror along X and shift origin to the component with role "FB"

```bash
python utils/transform_template.py -i template.yaml -o template_mirrored.yaml --mirror-x --set-origin-by-component-role FB
```

#### Explicit origin shift

```bash
python utils/transform_template.py -i template.yaml -o template_shifted.yaml --origin-x 1.5 --origin-y -2.0
```

### `generate_10cl006.py` – config generator for 10CL006YE144C8G

A ready‑to‑run script (not an example, actually used in the project). A single source of data – the `BANKS` table (pad/shift/rotation per power rail of the FPGA) and `CLUSTER_MAP` (net → `Cluster` name) inside the file – from which three derived artefacts are generated.

#### Syntax

```bash
python utils/generate_10cl006.py
```

No arguments – output paths are hard‑coded inside the script (see `main()`); the `BANKS`/`CLUSTER_MAP` tables and anchor toggles (`USE_ANCHOR_ROLE`, `THERMAL_USE_ANCHOR_ROLE`) are edited directly in the source.

#### What it generates

| File | Purpose |
|------|---------|
| `profiles/generated/10CL006YE144C8G.yaml` | Rules‑based config (`ManualSpoke`/`Rule`) – self‑contained and apply‑ready, uses the old inline (approximate) `templates:`. |
| `profiles/generated/10CL006YE144C8G.clone_placements.yaml` | Equivalent geometry as `clone_placements:` (`ClonePlacement`). **Since 2026-07-26, `Rule`/`ManualSpoke` can also clone tracks** (see `spoke_layout.py`/`TemplateTrack`) – keeping this path around is now worthwhile for anchor resolution via `anchor_pad`/`anchor_cluster` and `{power_net}` placeholders through `params`, which `Rule` does not resolve, not for tracks. Requires template `cap_pair_standard_clone` from `profiles/templates/3ch-awg-tia.yaml` (via `templates_file`). Not automatically included – copy the block manually into `profiles/3ch-awg-tia.yaml` after verifying with `--dry-run`. |
| `profiles/generated/10CL006YE144C8G.cluster_table.md` | Table `net \| pad \| cluster` (`FPGA_PWR_BANK/<pad>`) – a cheat sheet for manually setting the `Cluster` field in Eeschema (Bulk Edit) for those pads for which proximity‑based resolution is not sufficient. |

`anchor_cluster` in `clone_placements` is always set, even if `Cluster` is not yet assigned in the schematic – the resolver (`clone_role_resolver`) simply skips that narrowing step and falls back to the next one. So the generated file can be run with `apply --dry-run --verbose` before marking `Cluster` in Eeschema; the log will show which pads need explicit tagging.

#### Example

```bash
python utils/generate_10cl006.py
# Generated: profiles/generated/10CL006YE144C8G.yaml
# Generated: profiles/generated/10CL006YE144C8G.clone_placements.yaml
# Generated: profiles/generated/10CL006YE144C8G.cluster_table.md
# Total spokes: 24
```

### `generate_config.py` – template stub (NOT a ready‑to‑run script)

Unlike `generate_10cl006.py`, this is a **template stub** for writing a similar generator for a new chip, not a working tool. The `TEMPLATE` in it is filled with ellipsis `[...]` instead of real geometry – running it "as is" fails with a YAML serialisation error:

```bash
python utils/generate_config.py
# ValueError: dictionary update sequence element #0 has length 1; 2 is required
```

Use it as a starting point: copy it, replace `TEMPLATE` with a real template (e.g., obtained via `extract`), fill the `FILTERS` list with your own `CloneParams` (`anchor_ref`/`anchor_pad`/`origin_x`/`origin_y`/`rotation_deg`/`params`/`nets`) – and only then run it.

---

## Diagnostic commands (debugging and testing)

These commands execute diagnostic scripts located in `kicadspoke/diagnostics/`. They help test IPC, geometry, field reading, flipping, etc.

### Check reading of the `Role` custom field

```bash
python -m kicadspoke.diagnostics.test_custom_fields C5 --field Role --verbose
```

### Test moving a single component

```bash
# Shift by +1 mm along X
python -m kicadspoke.diagnostics.test_move_one_cap C5 --delta-mm 1.0

# Revert the shift
python -m kicadspoke.diagnostics.test_move_one_cap C5 --revert
```

### Test component flip

```bash
python -m kicadspoke.diagnostics.test_flip_one_cap C6
```

### Test creating a single via

```bash
# Create a via next to C5
python -m kicadspoke.diagnostics.test_create_one_via C5 --offset-mm 1.2

# Remove the last created via
python -m kicadspoke.diagnostics.test_create_one_via --remove
```

### Test for KiCad crash on first write (issue #24966)

```bash
# Read‑only (no writes) – safe if KiCad is open
python -m kicadspoke.diagnostics.diagnose_first_write_crash --until 8

# Full test (reads + write) – may crash KiCad (use with caution)
python -m kicadspoke.diagnostics.diagnose_first_write_crash

# Test with a 30‑second pause before the write (checks the race hypothesis)
python -m kicadspoke.diagnostics.diagnose_first_write_crash --delay 30
```

### Display information about selected components

```bash
python -m kicadspoke.diagnostics.get_selected_component
```

### Get a pad's bounding box

```bash
python -m kicadspoke.diagnostics.get_pad_bbox --ref IC1 --pad 17
```

### Analyze keepout and via positions

```bash
python -m kicadspoke.diagnostics.diagnostic_keepout 10CL006YE144C8G.yaml
```

---

## Usage recommendations

1. **Before the first run** – use `extract` on a correctly placed instance to obtain a template. Use JSON format for convenient `templates_file` integration.
2. **Check your configuration** with `--dry-run` to verify positions, vias, and tracks.
3. **For debugging** – enable `--verbose` and log to a file.
4. **When handling multiple clones in selection mode** – use `--clone-placement` to process them one at a time.
5. **If KiCad crashes** on the first run – close the schematic editor or make an interactive edit in PCB before launching (workaround for issue #24966).
6. **For hierarchical projects** – use `clone-extract` before writing ClonePlacement to get exact net names and twin refdes.
7. **Store templates separately** – use `templates_file: templates.json` in the main config to keep geometry out of the main file.
8. **Transform templates** with `transform_template.py` instead of manual coordinate recalculation.

---

## Built‑in help

```bash
python kicadspoke_cli.py --help
python kicadspoke_cli.py apply --help
python kicadspoke_cli.py extract --help
python kicadspoke_cli.py undo --help
python kicadspoke_cli.py clone-extract --help
```

---

## Common errors and solutions

| Error | Possible cause | Solution |
|-------|----------------|----------|
| `BoardNotFoundError` | KiCad is not running or no board is open. | Open the project in KiCad and call `adapter.refresh_board()`. |
| `ComponentNotFoundError` | The specified `anchor_ref` is not found on the board. | Check the refdes in your config. |
| `ValidationError: not enough components for roles` | Not enough components with the `Role` field for the given net. | Add the `Role` field to the required components in the schematic and run Update PCB. |
| `ValidationError: resolved via net not found` | Typo in `params` or `net_overrides`. | Verify net names in the config against the schematic. |
| `ConnectionError` during write | KiCad crashed (known issue #24966) or is stuck. | Close the schematic editor or make an interactive edit in PCB, then restart. |
| `KiCad crash on first launch` | Schematic editor open and no interactive edits made. | Workaround: close the schematic or move a component in PCB and save. |
| `Cannot find via/track` during undo | The object was manually deleted. | Undo skips missing objects and continues. |

---

## Quick command examples

### Place decoupling capacitors for an FPGA (master config for the board)

```bash
python kicadspoke_cli.py apply profiles/3ch-awg-tia.yaml --verbose --log-file logs/placer.log
```

### Regenerate generated configs/cluster table for 10CL006

```bash
python utils/generate_10cl006.py
```

Then run `apply profiles/3ch-awg-tia.yaml --dry-run --verbose` to verify that the new geometry resolves as expected (see the `generate_10cl006.py` section above).

### Undo placement

```bash
python kicadspoke_cli.py undo --verbose
```

### Extract a template to JSON (recommended format)

```bash
python kicadspoke_cli.py extract --name pi_filter_4 --output templates/pi_filter_4.json \
  --origin-by-via-net '+3V3_VCCIO' \
  --param PWR_IN='+3V3' --param PWR_OUT='+3V3_VCCIO' \
  --net-template '+3V3_VCCIO={PWR_OUT}' --net-template '+3V3={PWR_IN}' \
  --verbose
```

### Apply a clone using an external template file

```bash
python kicadspoke_cli.py apply config_with_templates_file.yaml --clone-placement fpga_filter_1v2_vccint
```

### Transform a template

```bash
python utils/transform_template.py -i templates/pi_filter_4.json -o templates/pi_filter_4_rotated.json --rotate 180 --set-origin-by-via-net '+3V3_VCCIO'
```

### Test KiCad for crashes

```bash
# Read‑only
python -m kicadspoke.diagnostics.diagnose_first_write_crash --until 8

# Full test (reads + write)
python -m kicadspoke.diagnostics.diagnose_first_write_crash

# With a 30‑second pause before write
python -m kicadspoke.diagnostics.diagnose_first_write_crash --delay 30
```

---

## License

All examples are distributed under the MIT license, the same as the main project.
