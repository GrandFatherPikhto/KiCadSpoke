# PyQt6 GUI

A persistent window meant to stay open alongside KiCad while you work — not a one-shot script like
the CLI. Wraps `kicadstamp.explore`/`kicadstamp.author`/`KiCadBoardAdapter`/`ApplyPipeline`
directly; nothing new is reimplemented here that the CLI doesn't already do. For the underlying
extraction/placement mechanics themselves, see [docs/config.md](config.md) (YAML shape) and
[docs/commands.md](commands.md) (CLI equivalents of every action below).

## Launching

```bash
python kicadstamp_gui.py [--timeout-ms 20000] [--verbose]
```

`--verbose` seeds the Log dock's Verbose checkbox (see below) so DEBUG-level detail is visible
from the first run instead of having to turn it on after something goes wrong.

## Layout

Eight docks, tabbed into two groups plus a status bar:

- **Left** (tabbed): **Components** (Role/Cluster tree) and **Cells** (extracted Cell list).
- **Right** (tabbed): **Bulk edit**, **Files**, **Extract**, **Placer**.
- **Bottom**: **Log**.
- **Status bar**: connection state, Reconnect/Refresh button, Always on top checkbox.

Nothing here pushes updates from KiCad — kipy 0.7.1 has no selection/board-change events, so
"live" means polled: a slow timer (~2s) only reconnects while disconnected and never rebuilds the
tree on its own (an earlier version did, and the visible flicker on an idle board was worse than
useless); a fast timer (~400ms) tracks the board's own selection and reflects it back into the
tree. Rebuilding the full snapshot happens only on an explicit action — the status-bar button
(**Reconnect** while disconnected, **Refresh** while connected).

## Components tree

Groups the live footprint snapshot by **Role** (flat) or **Cluster** (hierarchical, split on `/` —
`Channel_1/PI_FILTER` nests under `Channel_1`, matching the segment-prefix matching used
throughout the config system). The grouping choice is remembered across restarts.

- Click a leaf (one component) or a group (everything under it) to select it **on the real
  board**.
- The reverse also works: selecting something in KiCad's own PCB editor highlights it here.
- **Filter** field matches ref/role/cluster; the **regex** checkbox switches from substring to a
  case-insensitive regex (an invalid pattern just flags the field red, it doesn't crash or hide
  everything).
- Clicking a **Cluster group node** (only in Cluster grouping, only a group — not a leaf) also
  fills the Placer dock's Cluster field, so you don't have to retype it.

## Cells tab

A flat list of Cell names read from whatever file is assigned the **Cells** role in Files (see
below). Click one to feed the **Placer** dock's Cell field.

## Bulk edit

Sets Role and/or Cluster on whatever is currently selected on the board (mouse in KiCad, or a tree
click) — one transaction for the whole batch. The combo boxes pre-fill with the selection's
existing value when it's uniform, show `(mixed — differs across selection)` when it isn't, and
autocomplete from every distinct Role/Cluster already used on the board. Blocks (doesn't write
anything) if applying would create a duplicate Role within a Cluster, anywhere on the board — not
just within the batch being edited.

## Files

A file tree (default root: `boards/`, changeable) for picking YAML/JSON config files, plus three
named **roles** other docks read their target file from:

| Role | Consumed by | What goes there |
|---|---|---|
| **Cells** | Extract (writes), Placer/Cells tab (reads) | The flat `{cell_name: {...}}` file `extract`'s output goes into (`cells_file:`/`cell_files:` shape). |
| **Extractor** | Extract | The structured root config `extract_profiles:` entries get written into. |
| **Placer** | Extract (wiring only), Placer | The structured root config `clone_placements:` entries get written into — the file you'd point a real `apply` run at. |

To assign a role: click a file in the tree, then **"Use selected"** on the role's row.

**Extractor and Placer can — and normally should — share one file**: both are the same
"structured root config" shape (`extract_profiles:`/`cell_files:`/`include:`/`clone_placements:`
as sibling keys). **Cells cannot share a file with either one**: a `cells_file`/`cell_files`
target is parsed as a flat mapping with no wrapper (every top-level key is read as a cell name —
see [docs/config.md](config.md)), so an `extract_profiles:`/`clone_placements:` key sitting in
that same file would itself be misread as a cell. The Files dock warns if you assign Cells the
same file as Extractor or Placer.

## Extract

Builds a `Cell` from whatever's currently selected on the board (components, vias, tracks) and
writes it into the Cells file — the GUI equivalent of `kicadstamp_cli.py extract`.

- **Cell name** — defaults to the current selection's Cluster, slugified (`PWR/DAC0` →
  `pwr_dac0`), if nothing's been extracted from this Cluster before; if an existing Cells/
  Extractor key already matches, that wins instead. Never overwrites something you've typed.
- **Origin** — Bounding box (default, lower-left corner of the selection) / Component role (+
  optional pad) / Via net.
- **Net aliases** — one row per net found on the selected components' pads. A non-empty alias
  becomes a `{PLACEHOLDER}` in the written Cell (feeds `params:` for round-trip resolution — see
  [docs/config.md](config.md) on `net_template`/`params`).
- **Net template role** — appears only when a component's pads touch **2 or more already-aliased
  nets** (a bridging part — inductor, ferrite bead, fuse spanning two rails). The tool can't guess
  which one is "the" role's net_template in that case; extraction is blocked until you pick.
- **Existing (click to reuse a name)** — two lists (Cells/Profiles) read from the currently
  assigned files. Clicking an entry reuses its name outright and pulls its saved net aliases,
  net-template-role picks, and origin settings back into the form (matched by alias, not by the
  literal net text, so it still works when reusing a profile for an analogous Cluster on a
  different rail — e.g. `+2V5` vs `-2V5`). Also happens automatically when the current selection's
  Cluster slug matches an existing key.
- **Also save as extract_profile** — additionally writes a replayable recipe (name/output/params/
  origin/net_template_role) into the Extractor file's `extract_profiles:` section, so the same
  extraction can be re-run later from the CLI (`kicadstamp_cli.py extract --profile <key>`)
  without retyping the alias mapping.
- If a **Placer** file is assigned, a successful extraction also makes sure that file's
  `cell_files:` list includes the Cells file and its `include:` list includes the Extractor file
  (deduplicated by resolved path) — the Placer file ends up ready to use what was just extracted.

## Placer

Builds and applies a `ClonePlacement` — the GUI equivalent of `kicadstamp_cli.py apply --only
<name>`. **This dock moves real footprints on the live board.**

- **Cell** — picked in the Cells tab (see above); the current pick is shown here.
- **Cluster** — the placement's name (also what gets clicked from the Components tree, see
  above).
- **Params** — one row per `{PLACEHOLDER}` found anywhere in the picked Cell's own YAML (auto-
  discovered, not hand-typed) — the literal net each placeholder should resolve to for *this*
  instance.
- **Origin**:
  - *Absolute XY* — a literal board position.
  - *Anchor (ref/role)* — position relative to an existing component: Ref **or** Role (mutually
    exclusive), optional Pad, optional Anchor cluster (narrows which same-Role component is meant,
    when there's more than one). Role and Anchor cluster are pick-from-list combo boxes,
    autocompleted from the live board; Ref is plain free text (this project prefers Role over
    refdes — Role survives re-annotation, refdes doesn't — Ref exists mainly for the rare case it's
    actually needed).
  - *Point* — position relative to a named `points:` entry.
  - Anchor/Point modes also take a flat XY **shift**.
- **Rotation / Layer / Mirror** — as in `ClonePlacement`'s own fields (see
  [docs/config.md](config.md)).
- **Redraw** — builds the placement, validates it, and actually runs it against the live board
  (loading the *real*, full Placer config first, so any other already-saved placement's vias/
  tracks are protected — not a synthetic single-placement preview). On success, the components
  that were actually placed are tagged `Cluster=<name>` (nothing else in the pipeline does this —
  see [docs/config.md](config.md) on `Cluster` being read-only during `apply`). Change a field,
  click Redraw again — idempotent, safe to repeat.
- **Save** — separately, writes the current form into the Placer file's `clone_placements:` list
  (replacing an existing entry of the same name, never duplicating). Redraw does **not** save by
  itself — look, adjust, Redraw again, and only Save once you're happy with the result. KiCad's own
  undo covers "moved something to the wrong place" — there's no separate movement log here.

Not covered by the GUI yet (all still reachable by hand-editing the saved YAML): `anchor_sheet`
narrowing, `points:`-name autocomplete, `refs:` explicit role→ref override, `by_selection` mode.

## Log

A read-only, copyable, searchable panel fed by a `logging.Handler` attached to the **root**
logger — every `logger.info`/`warning` anywhere in the backend shows up here, not just things this
GUI writes itself. **Verbose** toggles this panel's own level between INFO and DEBUG (the
console/file logging `kicadstamp_gui.py` was launched with, if any, is untouched). **Find** /
**Prev** / **Next** search the accumulated text; **Clear** empties it.

## What's remembered between restarts

Plain JSON in `gui/gui_state.json` (gitignored, human-readable, deliberately not Qt's own
`QSettings`/`saveGeometry()` blob): window position/size, Always on top, Components tree grouping,
the Files dock's root directory and last click, and all three file-role assignments
(Cells/Extractor/Placer).

## Tests

`tests/gui/` — offscreen (`QT_QPA_PLATFORM=offscreen`, set automatically), no live KiCad
connection needed, part of the default `pytest` run. Board-mutating logic (Placer's Redraw) is
tested with `ApplyPipeline`/`PlacementPlanner` mocked — it verifies the dock builds the right
config and calls the pipeline correctly, never that it actually moves anything. See
`tests/gui/conftest.py` for the fixtures (`qapp`, `main_window`, `isolated_settings`, `log_dock`).
