# `kicadspoke/diagnostics/` – Diagnostic Scripts

## Purpose

The `kicadspoke/diagnostics/` directory contains a set of diagnostic and debugging scripts that help
developers and advanced users verify **KiCadSpoke**'s behaviour, debug configurations, analyse geometry,
and test individual IPC operations. The scripts use the current `kicadspoke` API (adapter, geometry,
config) and do not depend on legacy modules.

All scripts require a **running KiCad instance** with an active board and are run from the project root
via `python -m`.

---

## Structure

```
kicadspoke/diagnostics/
├── diagnose_first_write_crash.py  # Diagnoses the KiCad crash on the first IPC write (issue #24966)
├── diagnostic_charset.py          # Finds non-ASCII characters (homoglyphs) in Role/Cluster board-wide
├── diagnostic_keepout.py          # Keepout and overlap analysis
├── get_pad_bbox.py                # Pad bounding box
├── get_selected_component.py      # Detailed info on selected components
├── get_selection.py               # List of selected objects
├── test_create_one_via.py         # Creates a single via
├── test_custom_fields.py          # Verifies reading the Role field
├── test_flip_one_cap.py           # Verifies flipping a single component
├── test_move_one_cap.py           # Verifies moving a single component
└── test_pad_mirror_convention.py  # Verifies the pad-mirroring convention
```

---

## Script descriptions

### `diagnose_first_write_crash.py`

Diagnoses the KiCad crash on the first IPC write (issue #24966). The full description, hypotheses H1-H3,
parameters, output, and dependencies live in a separate document, since this is the only script in this
set tied to one specific filed bug with its own dedicated hunting workflow:
**[diagnose_first_write_crash.md](diagnose_first_write_crash.md)**. A description of both related bugs
(#24966/#24970) and the rest of the hunting toolkit lives in [crash_hunting.md](crash_hunting.md).

```bash
python -m kicadspoke.diagnostics.diagnose_first_write_crash --until 8   # reads only, safe
python -m kicadspoke.diagnostics.diagnose_first_write_crash             # full test, may crash KiCad
```

---

### `diagnostic_charset.py`

**Purpose:**
Walks every footprint on the board (by default the `Role` and `Cluster` fields, configurable via
`--fields`) and looks for characters outside printable ASCII (`0x20`–`0x7E`). The script exists because of
a live finding on `3CH-AWG-TIA`: three components (`C3`, `C9`, `C170`) had a `Role` value whose first
letter was the Cyrillic "С" (`U+0421`) instead of the Latin "C" (`U+0043`) — apparently the keyboard
layout had switched to Russian mid-way through typing the field value in Eeschema Bulk Edit. The letters
are visually indistinguishable in almost any font, but `component_pool.py`/`clone_role_resolver.py`
compare `Role` with strict character-by-character equality — a component with this typo matches no rule
looking for the "correct" (Latin) role, and the mismatch is essentially impossible to spot by eye.

**Usage:**
```bash
# Check Role and Cluster board-wide (default)
python -m kicadspoke.diagnostics.diagnostic_charset

# Check a different set of fields
python -m kicadspoke.diagnostics.diagnostic_charset --fields Role,Cluster,Value

# Also print clean fields (not just findings)
python -m kicadspoke.diagnostics.diagnostic_charset --verbose
```

**Parameters:**
- `--fields` – comma-separated list of fields, no spaces (default `Role,Cluster`).
- `--timeout-ms` – IPC timeout (default `20000`).
- `--verbose` – also log "clean" fields (no findings).

**Output:**
A list of findings: refdes, field name, the value in full, and for each "bad" character — its position in
the string, the character itself, its codepoint (`U+XXXX`), and its Unicode name (`unicodedata.name`).
Exit code is `0` if nothing was found, `1` if at least one field had a finding (handy as a standalone step
before `apply` or in CI:
`python -m kicadspoke.diagnostics.diagnostic_charset || echo "suspicious characters found in Role/Cluster"`).

**Dependencies:**
`kicadspoke.kicad.adapter.KiCadBoardAdapter` (`get_footprints`/`get_field_value`), `unicodedata` from the
standard library.

---

### `diagnostic_keepout.py`

**Purpose:**
Loads the config, plans the placement, builds keepout from IC and component pads, then checks whether
component and via positions fall inside the keepout. Prints detailed information for debugging.

**Usage:**
```bash
python -m kicadspoke.diagnostics.diagnostic_keepout <config.yaml>
```

**Output:**
- A list of keepout rectangles with coordinates.
- Status (INSIDE/CLEAR) for each component.
- Status for each via (spoke and component).

**Dependencies:**
`kicadspoke.config`, `kicadspoke.kicad.adapter`, `kicadspoke.placement.planner`, `kicadspoke.geometry.keepout`.

---

### `get_pad_bbox.py`

**Purpose:**
Prints a pad's bounding box (size, position) and the copper layer's size (if available). Useful for
verifying pad geometry.

**Usage:**
```bash
python -m kicadspoke.diagnostics.get_pad_bbox --ref IC1 --pad 17 --verbose
```

**Parameters:**
- `--ref` – component refdes (default `IC1`).
- `--pad` – pad number (shows all if omitted).
- `--timeout` – IPC timeout (ms).
- `--verbose` – verbose output.

**Output:**
- Bbox size (mm).
- Bbox position.
- Copper layer size (if available).

**Dependencies:**
`kicadspoke.kicad.adapter`, `kicadspoke.geometry.thermal_grid`.

---

### `get_selected_component.py`

**Purpose:**
Prints detailed information about the selected components: refdes, value, footprint, position, angle,
size (bbox), the list of pads (numbers, nets, positions, sizes), and the `Role` field. Handles groups
(Group) correctly.

**Usage:**
Select components in the PCB editor, then run:
```bash
python -m kicadspoke.diagnostics.get_selected_component
```

**Output:**
A table with information about each component and its pads.

**Dependencies:**
`kicadspoke.kicad.adapter` (uses `get_selected_items`).

---

### `get_selection.py`

**Purpose:**
A simple diagnostic script that lists all selected objects (footprints, pads, tracks, vias) with their
types and key parameters.

**Usage:**
Select objects in the PCB editor, then run:
```bash
python -m kicadspoke.diagnostics.get_selection
```

**Output:**
A list of objects with type and key properties.

**Dependencies:**
`kicadspoke.kicad.adapter` (uses `get_selected_items`).

---

### `test_create_one_via.py`

**Purpose:**
Creates a single via next to a given component. Saves the UUID of the created via to
`.last_test_via.json` for later removal. Lets you verify `create_items` and transactions work.

**Usage:**
```bash
# Create a via
python -m kicadspoke.diagnostics.test_create_one_via C5 --offset-mm 1.2

# Remove the last created via
python -m kicadspoke.diagnostics.test_create_one_via --remove

# Remove a specific via by UUID
python -m kicadspoke.diagnostics.test_create_one_via --remove <uuid>
```

**Parameters:**
- `--offset-mm` – offset from the component's center (mm).
- `--net` – the via's net (default `GND`).
- `--drill-mm` – drill diameter.
- `--diameter-mm` – outer diameter.
- `--timeout-ms` – IPC timeout.

**Dependencies:**
`kicadspoke.kicad.adapter`.

---

### `test_custom_fields.py`

**Purpose:**
Verifies reading a component's custom field via IPC. Prints all texts and fields (`Field`) of the
component, then looks for a field with a given name (default `Role`). Critical for verifying that roles
work correctly.

**Usage:**
```bash
python -m kicadspoke.diagnostics.test_custom_fields C5 --field Role
```

**Parameters:**
- `--field` – name of the field to look for (default `Role`).
- `--timeout-ms` – IPC timeout.
- `--verbose` – verbose output.

**Output:**
- A list of all of the component's fields and texts.
- The value of the requested field (or a message that it wasn't found).

**Dependencies:**
`kicadspoke.kicad.adapter` (uses `get_field_value`).

---

### `test_flip_one_cap.py`

**Purpose:**
Verifies a "real" component flip via the GUI action `pcbnew.InteractiveEdit.flip`. Prints the component's
state before and after the flip. Lets you confirm the flip works correctly (layer and mirroring).

**Usage:**
```bash
python -m kicadspoke.diagnostics.test_flip_one_cap C6
```

**Parameters:**
- `--timeout-ms` – IPC timeout.

**Output:**
Component state (layer, position, angle) before and after the flip.

**Dependencies:**
`kicadspoke.kicad.adapter` (uses `flip_selected` and `refresh_board`).

---

### `test_move_one_cap.py`

**Purpose:**
Verifies moving a single component a given distance along the X axis. Lets you isolate transaction
problems (`begin_commit`, `update_items`, `push_commit` hanging).

**Usage:**
```bash
# Move by +1 mm
python -m kicadspoke.diagnostics.test_move_one_cap C5 --delta-mm 1.0

# Move it back
python -m kicadspoke.diagnostics.test_move_one_cap C5 --revert
```

**Parameters:**
- `--delta-mm` – shift amount (mm).
- `--revert` – shift in the opposite direction.
- `--timeout-ms` – IPC timeout.

**Output:**
Execution time for each step (connect, begin_commit, update_items, push_commit) in milliseconds.

**Dependencies:**
`kicadspoke.kicad.adapter`.

---

### `test_pad_mirror_convention.py`

**Purpose:**
Verifies the mirroring convention for a pad's local offset on flip (used in
`geometry/pad_projection.py`). Runs two steps: a 90° rotation without flipping (checks the base formula),
then a flip and comparison of three candidates (mirror across X, mirror across Y, no mirror). Restores
the component to its original state afterwards.

**Usage:**
```bash
python -m kicadspoke.diagnostics.test_pad_mirror_convention C6 --pad 2
```

**Parameters:**
- `--pad` – pad number to track (default `2`).
- `--timeout-ms` – IPC timeout.

**Output:**
- Base-formula discrepancy after the rotation.
- Distances for the three candidates after the flip.
- The winner (mirror across X, across Y, or no mirror).

**Dependencies:**
`kicadspoke.kicad.adapter`, `kicadspoke.geometry.pad_projection` (helper).

---

## General recommendations

- **Run with `--verbose`** for debugging, if the script supports the flag.
- **Always run from the project root** using `python -m kicadspoke.diagnostics.<script_name>`.
- **Make sure KiCad is open** with the relevant board active.
- For scripts that work with the selection, select the relevant objects in the PCB editor **before**
  running them.

---

## Notes

- The scripts **do not modify the board** (except for `test_move_one_cap`, `test_flip_one_cap`,
  `test_create_one_via`, which can mutate it). Use them on test boards or make sure you have a backup.
- `diagnose_first_write_crash.py` does not mutate the board (the write is a no-op), but on an affected
  session (see issue #24966) the write attempt itself can **crash the KiCad process entirely**. Save open
  files before running the full ladder (without `--until 8`).
- `test_move_one_cap`, `test_flip_one_cap`, and `test_create_one_via` **do not use** the placement
  registry, so they are not undone by the `undo` command.
- For a full placement diagnosis, run `diagnostic_keepout.py` with the actual config.

---

## Extending the diagnostic scripts

To add a new diagnostic script:

1. Place it in `kicadspoke/diagnostics/`.
2. Use the current `kicadspoke` API (adapter, geometry, config).
3. Add a description to this document.
4. Make sure the script doesn't modify the board (or warns about it), unless it's meant to mutate.

---

## License

The diagnostic scripts are distributed under the MIT license, same as the main project.
