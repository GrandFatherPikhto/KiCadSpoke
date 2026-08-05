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
- **Right** (tabbed): **fieldstool**, **Files**, **Extract**, **Placer**.
- **Bottom**: **Log**.
- **Status bar**: connection state, Reconnect/Refresh button, Always on top checkbox, Tray icon
  checkbox, Open fieldstool button, KiCad processes... button.

The **KiCad processes...** button opens a picker listing every running `kicad.exe` (PID, Windows
"Not Responding"/"Running" status, window title) — a shortcut for "look in Task Manager, pick the
stuck one, force-close it by hand" (added after a crashed/frozen KiCad process, left running
alongside a fresh one, blocked the fresh one's IPC connection). Deliberately never automatic: kipy
has no way to check any KiCad process for unsaved changes, so closing one is always something a
human picks and confirms here, never a heuristic decision the tool makes on its own (see
`gui/kicad_processes_dialog.py`).

Nothing here pushes updates from KiCad — kipy 0.7.1 has no selection/board-change events, so
"live" means polled: a slow timer (~2s) only reconnects while disconnected and never rebuilds the
tree on its own (an earlier version did, and the visible flicker on an idle board was worse than
useless); a fast timer (~400ms) tracks the board's own selection and reflects it back into the
tree. Rebuilding the full snapshot happens only on an explicit action — the status-bar button
(**Reconnect** while disconnected, **Refresh** while connected).

## Components tree

Two data sources, one tree, toggled by the **Not yet applied** checkbox:

- **Unchecked (default) — live board.** Groups the live footprint snapshot by **Role** (flat) or
  **Cluster** (hierarchical, split on `/` — `Channel_1/PI_FILTER` nests under `Channel_1`, matching
  the segment-prefix matching used throughout the config system). Click a leaf (one component) or a
  group (everything under it) to select it **on the real board**; the reverse also works — selecting
  something in KiCad's own PCB editor highlights it here. Clicking a **Cluster group node** (only in
  Cluster grouping, only a group — not a leaf) also fills the Placer dock's Cluster field.
- **Checked — not yet applied (schematic).** Same tree, same grouping/filter UI, but the data comes
  from the [fieldstool tab](#fieldstool-tab)'s own already-parsed `.kicad_sch` component list — and,
  since 2026-08-03, only the refs that currently have an actual Role/Cluster discrepancy between the
  schematic and the live board (the same diff Pending changes shows). A component whose schematic and
  board values already agree — including right after a successful Apply — no longer shows up here at
  all (found live: components used to stay listed even with nothing left to apply, which read as a
  bug once Pending changes existed alongside this view). A component never seen on the live board this
  session (no live snapshot entry to compare against) is also not shown, even if it's genuinely on the
  schematic — there's nothing to diff it against. Divergent multi-unit refs (units disagreeing on
  Role/Cluster within the schematic itself) get a ⚠ marker. Clicking a leaf or group here stages that
  target into fieldstool (same as clicking used to inside fieldstool's own, now-retired, internal
  tree) and brings the fieldstool tab to front. Refreshes automatically whenever fieldstool's own
  Rescan runs, or the schematic-vs-board diff changes (a fresh poll tick, or a Stage/Clear all write).

The grouping choice and the live/schematic toggle are both remembered across restarts. **Filter**
matches ref/role/cluster in either mode; **regex** switches from substring to a case-insensitive
regex (an invalid pattern just flags the field red, it doesn't crash or hide everything).

## Cells tab

A flat list of Cell names read from whatever file is assigned the **Cells** role in Files (see
below). Click one to feed the **Placer** dock's Cell field.

## fieldstool tab

The first right-hand tab embeds [fieldstool](fieldstool.md)'s own GUI whole, wrapped in one dock
(`gui/docks/fieldstool_dock.py`) — there is no second process/window, this is now the only way
fieldstool runs (a standalone `fieldstool_gui.py` entry point existed until 2026-08-02, retired as
pure duplication of this tab). Its **Pending changes** dock (2026-08-03: the schematic-vs-board
Role/Cluster diff — see [fieldstool.md](fieldstool.md#2-apply-kicad-must-be-closed)) is shared with
the main window, tabbed with **Log** at the bottom, not a dock local to this tab anymore. It shares
this GUI's own `BoardConnection` and single 2s/400ms poll (one kipy client, one REQ socket — a
second independent timer on the same connection would interleave requests mid-flight). It has no
Components tree of its own — the main [Components tree](#components-tree)'s "Not yet applied" mode
covers that job when embedded here (fieldstool's own tree, `fieldstool/gui/tree.py`, was retired
2026-08-01).

This replaced **Bulk edit** (also retired 2026-08-01), which used to set Role/Cluster directly over
live PCB IPC from this tab's slot with no further persistence step — that write was PCB-only and got
silently reverted by KiCad's own "Update PCB from Schematic", since `Role`/`Cluster` actually
originate in the schematic symbol. fieldstool's own Stage button (and the main Components tree's
Clear all/Delete selected) write over the same kind of live IPC today too, but Apply's schematic
diff is what actually persists the change into `.kicad_sch` — the missing step Bulk edit never had.
fieldstool edits `.kicad_sch` directly instead, which survives that resync — see
[fieldstool.md](fieldstool.md) for the full design and why it needs KiCad closed to Apply.

## Files

A file tree (default root: `boards/`, changeable) for picking YAML/JSON config files, plus three
named **roles** other docks read their target file from:

| Role | Consumed by | What goes there |
|---|---|---|
| **Cells** | Extract (writes), Placer/Cells tab (reads) | `extract`'s output goes into this file's `cells:` key. |
| **Extractor** | Extract | The structured root config `extract_profiles:` entries get written into. |
| **Placer** | Extract (wiring only), Placer | The structured root config `clone_placements:` entries get written into — the file you'd point a real `apply` run at. |

To assign a role: click a file in the tree, then **"Use selected"** on the role's row.

**All three roles can share one file** — all three are the same "structured root config" shape
(`extract_profiles:`/`cells:`/`include:`/`clone_placements:` as sibling keys, since `cells_file:`/
`cell_files:` were folded into `include:` on 2026-08-02 — see [docs/config.md](config.md)). A
dedicated file per role is just the default habit, not a requirement enforced anywhere.

## Config tree

A tree mirroring the actual `include:` file graph from a single root config file — pick it via
**Open Root file...**/**New Root file...**/the **Recent** dropdown. Every file node shows its own
sections (Cells/Clone placements/Thermal via arrays/Points/Rules/Extract profiles/Clone profiles)
and its own included files, recursively.

Right-click any entry for:
- **Rename...** — renames the entry; for Cells/Points, also rewrites every reference to it
  (`cell:`/`anchor_point:`) anywhere in the whole include: graph, not just the file it's declared in.
- **Delete...** — removes the entry, after backing up the whole file it lived in (timestamped, next
  to the original — a repeated delete never overwrites an earlier backup). For Cells/Points, the
  whole include: graph is scanned for references first; if any are found, the confirmation lists
  them and asks whether to delete those referencing entries too (declining cancels the delete
  entirely rather than leaving a dangling reference).
- **Export.../Export selected...** — select one or more entries (multi-select is enabled just for
  this) and copy them into a separate file via a Save dialog. The originals are left untouched. If
  the target file already has content, you're asked whether to merge the exported entries into it
  or overwrite the whole file.

Right-click a file node for **Add cell.../Add point.../Add rule.../Add placer.../Add thermal via
pad.../Add included file...**, plus **Remove this file** (soft-disables its `include:` entry,
doesn't delete the file) when it's not the root.

## Extract

Builds a `Cell` from whatever's currently selected on the board (components, vias, tracks) and
writes it into the Cells file — the GUI equivalent of `kicadstamp_cli.py extract`.

**Origin**/**Net aliases**/**Net template role**/**Existing** below live in a tab widget
(2026-08-04: previously stacked in one long column, whose minimum height was the SUM of every
section's own — the dock couldn't shrink below that even when most of it didn't apply right now).
A `QTabWidget` only sizes for the current page, so the dock resizes freely; **Net template role**'s
tab is hidden outright (not just its content) until it actually applies.

- **Cell name** — defaults to the current selection's Cluster, slugified (`PWR/DAC0` →
  `pwr_dac0`), if nothing's been extracted from this Cluster before; if an existing Cells/
  Extractor key already matches, that wins instead. Never overwrites something you've typed.
- **Origin** — Bounding box (default, lower-left corner of the selection) / Component role (+
  optional pad) / Via net.
- **Net aliases** — one row per net found on the selected components' pads. A non-empty alias
  becomes a `{PLACEHOLDER}` in the written Cell (feeds `params:` for round-trip resolution — see
  [docs/config.md](config.md) on `net_template`/`params`). Each row also has a **"Rule net (null)"**
  checkbox (2026-08-05), mutually exclusive with the alias field — checking it writes that net's
  via/track as `net: null` instead, so a cell placed via `rules:`/ManualSpoke inherits whichever
  Rule's own net it's placed under (see [docs/config.md](config.md) on `rule_nets:`) — the mechanism
  for reusing the SAME cell across several Rules on different power rails, which `{PLACEHOLDER}`
  aliasing can't do here (ManualSpoke has no `params:` to resolve a template against).
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
  `include:` list includes both the Cells file and the Extractor file (deduplicated by resolved
  path) — the Placer file ends up ready to use what was just extracted.

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
narrowing, this dock's own Point-chain field has no name autocomplete (unlike the new Points panel
below, which does), `refs:` explicit role→ref override, `by_selection` mode.

## Project

(Tab labeled "Project" — Denis, 2026-08-05: "давай не root, а project"; the panel underneath is
still called RootMetadataDock in code, since it edits the project's ROOT config file, same concept
the Config tree's "Open Root file..." uses — only the displayed label changed.)

Edits the project's root-config-only scalar keys: Layer/Place components/Skip existing components
(shown above the tabs, as general project settings), then three tabs — **Files** (Registry path/
Track registry path/Log file/Operation log dir), **Schematics** (Schematic dir/Schematic files),
**Via** (the four `via_search_*`/`via_keepout_clearance_mm` fields) — split 2026-08-05 for the same
"dock too tall to resize" reason as Extract's own tabs above.

Always targets the project's single root file — the one opened via "Open Root file..."/"New Root
file..."/the Recent dropdown (see the Config tree in `gui/docks/config_tree.py`) — regardless of
which included file is currently browsed in that tree. Browsing into an included file does not
retarget this panel: these fields are only valid on an actual root (an included file setting any of
them is fatal at load — see [docs/config.md](config.md)), and a project only ever has one.

## Points

Edits a named `points:` entry (see [docs/config.md](config.md) on the Point schema) — a reusable
anchor other `anchor_point:` references (Placer's own Point origin mode, Rule/ThermalViaArrayConfig)
point at by name. Added 2026-08-05 after noticing how closely Point's own shape already matches
Placer's Origin widget.

- **Origin** — same three mutually exclusive bases as Placer's own Origin combo: **Absolute XY** /
  **Anchor (ref/role)**, now including a **Sheet** field (Denis: "нужен anchor_sheet в этой
  панели") alongside Ref/Role/Pad/Anchor cluster / **Point** (chain to another point by name — this
  field IS autocompleted, from the current file's own `points:` keys, closing the "points:-name
  autocomplete" gap the Placer section above still has for its own Point field).
- **Shift X/Y** — flat mm offset on top of the Anchor/Point base (not available on Absolute XY —
  there, just edit the coordinate directly).
- **Resolve** — computes where this point (and whatever it chains through) resolves to RIGHT NOW,
  without writing anything or moving anything on the board (a Point has no physical effect of its
  own, unlike Placer's Redraw) — shows the literal X/Y in mm, and, if it resolved through a live
  footprint, selects that footprint on the board (the same highlight the Components tree's own
  click-to-select already uses). An unrelated OTHER point in the same file that's currently broken
  is silently skipped rather than blocking this preview — deliberately more lenient than a real
  `apply` run's all-or-nothing config validation. Sheet-based narrowing is not yet wired into this
  preview specifically (it needs the project's `schematic_dir`, a second file dependency this first
  pass deferred) — Sheet is still saved correctly for a real `apply` run, which does build that
  narrowing properly.
- **Save** — writes into the target file's `points:` section (a dict keyed by name, unlike
  Placer/Thermal via's list-of-dicts sections — an existing name is replaced in place, not
  duplicated).

## Rules

Edits a `rules:` entry (see [docs/config.md](config.md) on Rule/ManualSpoke) — one shared anchor
(no `xy` mode here, unlike Points/Placer — only **Anchor (ref/role, + Sheet/Cluster)** or **Point**)
plus an ORDERED list of spokes, each placing a Cell at a specific pad of that anchor with its own
hand-tuned shift/rotation. Added 2026-08-05 after Denis connected `fpga_spokes.yaml`/
`fpga_cap_pair_spoke.yaml` to a real project and hit the long-standing "Rules has no edit form" gap.

**Net**/**Origin**/**Spoke** live in a tab widget (2026-08-05, same "a stacked `QVBoxLayout`'s
minimum height is the SUM of every section's own" fix as Extract/Project): Net carries the rule's
own Net/Name/Retired/Skip; Origin carries the anchor-mode combo and its two rows; Spoke carries
every field the detail row below the table writes into. The spokes table itself, its move/Add/
Update/Remove row, and Redraw/Save stay outside the tabs — they act on the whole rule, not one tab.

- **Spokes table + detail row below** — picked over putting spokes in the shared Config tree
  (a spoke has no name field for a tree leaf label, spoke ORDER is semantically significant — the
  component pool consumes spokes in list order — and a table's columns show every spoke's shift/
  rotation/cluster at a glance). The table itself is read-only; all editing goes through the row
  below it and its own **Add spoke** / **Update selected** / **Remove selected** / **Move up** /
  **Move down** buttons — a table row can never drift from what was actually validated and stored.
- **Cell** (per spoke) is a searchable combo listing every `cells:` key reachable from the
  project's root via `include:` — not just this file's own, since a spoke's cell routinely lives in
  a different file than the rule using it. **Point** (the rule's own anchor, not per-spoke — a
  spoke always anchors to a pad on THIS rule's own anchor) is populated the same whole-graph way.
  Both need the project's root, wired the same way Project's own panel does.
- **Redraw rule** — the whole rule, all non-skipped spokes, same replace-by-identity +
  `ApplyPipeline(only=[...])` shape as Thermal via's own Redraw.
- **Redraw selected spoke** — same, but every OTHER spoke in the copy handed to the pipeline gets a
  temporary `skip: true` injected (never written back — Save is unaffected) — sound because spoke
  resolution shares ONE component pool per net across the whole rule, so a single spoke can't be
  resolved in total isolation, but the pipeline can be told to skip every spoke except the one
  you're checking, which `skip:` already exists to do.
- **Save** — writes the whole rule into the target file's `rules:` list, matched by name if set,
  else net (`rules:` is the one list section without a required `name:` — see
  [docs/config.md](config.md)'s `rule_effective_name`).

## Log

A read-only, copyable, searchable panel fed by a `logging.Handler` attached to the **root**
logger — every `logger.info`/`warning` anywhere in the backend shows up here, not just things this
GUI writes itself. **Verbose** toggles this panel's own level between INFO and DEBUG (the
console/file logging `kicadstamp_gui.py` was launched with, if any, is untouched). **Find** /
**Prev** / **Next** search the accumulated text; **Clear** empties it.

## Tray icon

The **Tray icon** status-bar checkbox creates an OS tray icon (a small programmatic icon, not a
binary asset — `gui/tray_icon.py`) with a menu: **Show/Hide**, **Open fieldstool**, **Quit**.
While checked, closing the window via its title-bar X hides it instead of quitting — reachable
again from the tray (single click/double-click, or the Show/Hide menu item). Unchecked, closing
behaves exactly as without a tray at all — a real quit. The tray menu's **Quit** always does a real
quit either way.

A single-instance guard (`gui/single_instance.py`, `QLocalServer`/`QLocalSocket`-based) means
running `kicadstamp_gui.py` a second time while one is already running doesn't open a second
window — it raises the existing one instead and exits immediately. This guard is always active,
independent of the Tray icon checkbox.

The checkbox state persists across restarts (`gui_state.json`'s `tray_enabled`) — if it was checked
in an earlier session, a later launch starts with it already checked, so the title-bar X hides
instead of quits from the very first close, with no on-screen reminder that this is what will
happen. On Windows specifically, a freshly-shown tray icon commonly lands in the hidden/overflow
tray (the "^" arrow next to the clock) rather than the visible row — "window vanished, no icon
anywhere I can see" does **not** mean the process died; check the overflow arrow first. If the icon
still can't be found, re-running `kicadstamp_gui.py` (the single-instance guard above) raises the
existing hidden window without starting a second process — no need to hunt it down in Task Manager.

## Open fieldstool

The status-bar **Open fieldstool** button (and the tray menu's identical item) un-hides the main
window if it was tray-hidden and brings the [fieldstool tab](#fieldstool-tab) to front — useful if
another right-hand tab is currently active, or if that dock was individually closed.

## What's remembered between restarts

Plain JSON in `gui/gui_state.json` (gitignored, human-readable, deliberately not Qt's own
`QSettings`/`saveGeometry()` blob): window position/size, Always on top, Tray icon, Components tree
grouping and its live/"Not yet applied" toggle, the Files dock's root directory and last click, and
all three file-role assignments (Cells/Extractor/Placer). fieldstool's own tab keeps its own
separate state file (`gui/fieldstool_gui_state.json`).

## Tests

`tests/gui/` — offscreen (`QT_QPA_PLATFORM=offscreen`, set automatically), no live KiCad
connection needed, part of the default `pytest` run. Board-mutating logic (Placer's Redraw) is
tested with `ApplyPipeline`/`PlacementPlanner` mocked — it verifies the dock builds the right
config and calls the pipeline correctly, never that it actually moves anything. See
`tests/gui/conftest.py` for the fixtures: `qapp`, `main_window` (a bare stub, for dock-level tests),
`real_main_window` (the real `MainWindow`, needed for tray/close/fieldstool-embedding tests),
`fieldstool_window` (a real `gui.fieldstool_window.MainWindow` with a fake connection, for the
fieldstool tab's own staging/Apply logic standalone), `isolated_settings`, `log_dock`.
