# fieldstool — bulk Role/Cluster set/rename in `.kicad_sch`

A standalone app, separate from `kicadstamp`/the [PyQt6 GUI](./gui.md): `fieldstool/` (library) +
`fieldstool_cli.py` (CLI) + `fieldstool_gui.py` (GUI). It edits `.kicad_sch` **directly, as text**
— not through KiCad's live IPC — because `Role`/`Cluster` custom fields originate in the schematic
symbol, and a PCB-only IPC write (what `gui/docks/bulk_field_editor.py` does) gets silently
reverted by KiCad's own "Update PCB from Schematic". See [Why a separate app](#why-a-separate-app)
below before assuming this could just be another GUI dock.

## Why a separate app

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
  That requirement is incompatible with the main GUI's always-open-alongside-KiCad model.
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

## `fieldstool_gui.py`

```bash
python fieldstool_gui.py [--timeout-ms 20000] [--verbose]
```

Splits the workflow into two phases with different KiCad requirements, matching the constraint
above:

### 1. Staging (KiCad open)

- **Pick root sheet** — points the tool at a project (same `root_sheet:` concept as the CLI).
  **Rescan** re-parses it (explicit action, not auto-polled — the schematic only changes when
  someone saves in Eeschema, not every couple of seconds).
- **Components tree** — same UX as the main GUI's [Components tree](./gui.md#components-tree)
  (group by Role/Cluster, filter with optional regex), but built from the **parsed schematic**, not
  a live PCB snapshot. One row per refdes (a shared multi-instance block expands to one row per
  member; a multi-unit refdes collapses to one row, flagged ⚠ if its units disagree on Role/Cluster
  — the schema allows this, nothing enforces it stays in sync).
- **Picking a target** — three interchangeable ways, all feeding the same edit panel:
  - click a tree **leaf** (one refdes),
  - click a tree **group** node (every refdes in that Role/Cluster group at once — this is how you
    do a "rename a whole group" without retyping refdes),
  - or just **select something in KiCad itself** (Eeschema *or* Pcbnew — a live, read-only
    connection watches the PCB selection, and since PCB/schematic selection cross-probe in KiCad,
    a schematic-side selection shows up here too).
- **Stage** — writes the current Role/Cluster form values into a JSON pending-changes queue, one
  entry per target refdes. Nothing touches `.kicad_sch` yet. The queue persists (`<root
  sheet>.pending.json`, next to the schematic) — safe to close the GUI and come back later.

### 2. Apply (KiCad must be closed)

- Checks for a running KiCad process — if found, shows an **instruction** dialog ("save your work
  and close KiCad, then Apply again"). This is never automated (see
  [Why a separate app](#why-a-separate-app)).
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
