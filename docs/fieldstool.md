# fieldstool — bulk Role/Cluster set/rename in `.kicad_sch`

`fieldstool/` (library) + `fieldstool_cli.py` (CLI) + `fieldstool.gui.main_window.MainWindow`,
embedded as the first tab of the main [PyQt6 GUI](./gui.md) (`gui/docks/fieldstool_dock.py`, see
[gui.md's fieldstool tab section](./gui.md#fieldstool-tab)) — the only way its GUI runs (a
standalone `fieldstool_gui.py` entry point existed 2026-08-01 through 2026-08-02, retired as pure
duplication of the embedded tab). It edits `.kicad_sch` **directly, as text** — not through KiCad's
live IPC — because `Role`/`Cluster` custom fields originate in the schematic symbol, and a PCB-only
IPC write gets silently reverted by KiCad's own "Update PCB from Schematic" (the main GUI used to
have exactly that as a dock, `BulkFieldEditorDock` — retired in favor of fieldstool taking its
place). See [Why a separate package](#why-a-separate-package) below — that's what stays separate,
not the window/process.

## Why a separate package

`fieldstool/` and `fieldstool/gui/` stay their own dependency-free packages (no import of
`kicadstamp`/`gui` in the offline core; `gui/docks/fieldstool_dock.py` is the one place that
imports `fieldstool.gui.main_window`, never the reverse) — this is about the write pipeline being
different, not about the window living in its own process:

- **A different, riskier write surface.** `kicadstamp`/`gui` only ever write through KiCad's live,
  transactional IPC (`BeginCommit`/`UpdateItems`/`EndCommit` — undoable in KiCad itself).
  `fieldstool` edits `.kicad_sch` as a file, directly — the same hazard class as KiCad bug #24966
  (touching a file KiCad may have open/cached), but worse, since it doesn't go through KiCad's
  live IPC at all.
- **KiCad must be closed to apply, and reopened to see the result.** A running KiCad process does
  not hot-reload an externally-modified schematic file. Checked exhaustively (kipy 0.7.1): there is
  **no application-level quit/close/shutdown call, and no "unsaved changes" check**, anywhere in
  `kipy.KiCad`, `kipy.Board`, `kipy.Schematic`, or any of its proto command definitions. So this
  can only ever be an **instruction** to the user ("close KiCad, then Apply") — never automated.
- **Point-edit, not parse→dump.** Edits are byte-offset text splices (regex + paren-balance
  matching for block boundaries), never a full `sexpdata` parse→`dumps()` round trip — there is no
  precedent that `sexpdata.dumps()` reproduces KiCad's own formatting byte-for-byte. `sexpdata` is
  only used to *self-verify* a write after the fact (see [Safety guards](#safety-guards)).

## `fieldstool_cli.py`

```bash
python fieldstool_cli.py set roles.yaml [--write] [--allow-non-ascii] [--force-with-kicad-running] [--verbose]
python fieldstool_cli.py rename renames.yaml [--write] [--allow-non-ascii] [--force-with-kicad-running] [--verbose]
```

Both subcommands are dry-run by default (print what would change, touch nothing); `--write` is
required to actually edit files.

### `set` — refdes → `{field: value}`

```yaml
root_sheet: ../test_boards/3CH-AWG-TIA/3CH-AWG-TIA.kicad_sch   # the PROJECT's top sheet, not a folder
fields:
  C51:
    Role: C_OUT_BULK
    Cluster: FPGA_PWR_BANK/17
  C52:
    Role: C_OUT_BULK
    Cluster: FPGA_PWR_BANK/26
```

`root_sheet:` is walked recursively (`(sheet (property "Sheetfile" "...") ...)` references,
diamond/cycle-safe) to find every reachable `.kicad_sch` — not a flat directory glob, so a stray
unrelated `.kicad_sch` sitting in the same folder is never picked up by mistake.

Two ways one refdes can appear in a file, both handled:
- **Multi-unit symbol** (e.g. a dual op-amp) — one refdes spans several separate `(symbol ...)`
  blocks (one per unit); all of them get edited.
- **Multi-instance sheet** (e.g. `Channel_1`/`2`/`3` all instancing one `channel_tpl.kicad_sch`) —
  several refdes share ONE `(symbol ...)` block, so the field is shared across all of them. If the
  config asks for **different** values for two such refdes, the format can't express that — fatal,
  not a silent pick of one of the two.

### `rename` — `field → {old_value: new_value}`, no refdes needed

```yaml
root_sheet: ../test_boards/3CH-AWG-TIA/3CH-AWG-TIA.kicad_sch
renames:
  Role:
    OLD_ROLE_A: NEW_ROLE_A
  Cluster:
    Old_Cluster_Name: New_Cluster_Name
```

Changes a value everywhere it currently occurs, across the whole schematic tree — you don't
enumerate which refdes are affected. Simpler than `set` in one respect: it can never hit the
multi-instance conflict above, since it always writes the *same* new value to every match. An
`old_value` that matched nothing anywhere is reported as a **warning**, not a fatal error — it's
just as likely a harmless re-run (renaming is idempotent) as a typo.

### Safety guards

- **Dry-run by default** — `--write` required to touch anything.
- **`.bak` + self-verify per file, independently** — before writing, the original text is copied to
  `<file>.bak`; after splicing, the result is re-parsed with `sexpdata` as a sanity check. If it
  doesn't parse, the file is restored from `.bak` and reported as failed — the rest of the batch
  still proceeds.
- **Non-ASCII refusal** (`--allow-non-ascii` to override) — guards the exact homoglyph-typo class
  that motivated this tool in the first place (a Cyrillic "С" instead of Latin "C" in a `Role`
  value).
- **Running-KiCad refusal** (`--force-with-kicad-running` to override) — a `.kicad_sch` that's
  open in Eeschema risks the edit being silently overwritten by KiCad's own next save.
- After `--write`, **"Update PCB from Schematic" in pcbnew is required** — the edit doesn't reach
  the board on its own.

## The fieldstool tab

`fieldstool.gui.main_window.MainWindow` is embedded whole as the first right-hand tab of the main
GUI (`gui/docks/fieldstool_dock.py`, see [gui.md's fieldstool tab
section](./gui.md#fieldstool-tab)) and shares that GUI's own `BoardConnection` and single 2s/400ms
poll — it never creates or polls a connection of its own (kipy's REQ socket allows exactly one
request in flight; a second independent timer on the same connection would interleave requests
mid-flight). This window has **no Components tree of its own** (retired 2026-08-01, along with the
separate `ComponentTreeDock` class that used to provide one — picking a target without a live board
selection exists via the main GUI's own Components tree, see below).

Splits the workflow into two phases with different KiCad requirements, matching the constraint
above:

### 1. Staging (KiCad open)

- **Pick root sheet** — points the tool at a project (same `root_sheet:` concept as the CLI).
  **Rescan** re-parses it (explicit action, not auto-polled — the schematic only changes when
  someone saves in Eeschema, not every couple of seconds) into `self._components` — one row per
  refdes (a shared multi-instance block expands to one row per member; a multi-unit refdes
  collapses to one row, flagged divergent if its units disagree on Role/Cluster — the schema allows
  this, nothing enforces it stays in sync).
- **Picking a target** — two ways, either fills the **Role**/**Cluster** combo boxes with the
  picked target(s)' existing value when it's uniform across all of them (read from the parsed
  schematic), and clears them — not left showing a stale value — when it differs:
  - **Select something in KiCad itself** (Eeschema *or* Pcbnew — the shared connection watches the
    PCB selection, and since PCB/schematic selection cross-probe in KiCad, a schematic-side
    selection shows up here too).
  - The main GUI's own [Components tree](./gui.md#components-tree), switched to **Not yet applied**
    mode, reads this window's `self._components` directly — click a **leaf** (one refdes) or a
    **group** node (every refdes in that Role/Cluster group at once, for a group-rename without
    retyping refdes) there instead, no live board selection needed. Clicking calls straight into
    this window's own `_on_tree_leaf_picked()`/`_on_group_picked()` and brings this tab to front.
- **Stage** — writes the current Role/Cluster form values into a JSON pending-changes queue, one
  entry per target refdes. Nothing touches `.kicad_sch` yet. The queue persists (`<root
  sheet>.pending.json`, next to the schematic) — safe to close the GUI and come back later.

### 2. Apply (KiCad must be closed)

- Checks for a running KiCad process — if found, shows an **instruction** dialog ("save your work
  and close KiCad, then Apply again"). This is never automated (see
  [Why a separate package](#why-a-separate-package)).
- If KiCad is closed: plans every staged edit through the exact same offline pipeline
  `fieldstool_cli.py set` uses, shows a confirmation summary, then writes (same `.bak`/self-verify
  guards as the CLI). On success, the pending queue is cleared and the tree is rescanned.

## Migrated from `tools/apply_role_cluster.py`

`fieldstool set` supersedes that script (folded in 2026-08-01, not left duplicated) — same
core logic (parsing, splicing, safety guards), just reorganized into a reusable library plus a
`rename` mode that didn't exist there. `root_sheet:` (hierarchy walk) replaces its
`schematic_dir:` (flat glob) — **not** backwards compatible, update old configs.

## Tests

`tests/test_fieldstool_*.py` (offline core — parsing, discovery, editing, `set`, `rename`, the
pending-changes registry, schematic-to-tree flattening) plus `tests/fieldstool_gui/` (the actual
Qt widgets, offscreen — same pattern as [`tests/gui/`](./gui.md#tests)), including a full
staging → Apply → write round trip against a synthetic `.kicad_sch`. No live KiCad needed anywhere.
