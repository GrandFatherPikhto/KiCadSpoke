# Scripting: `explore` and `author`

Two additive, optional Python modules for using kicadspoke as a library instead of (or alongside)
the CLI/YAML workflow — nothing here changes the YAML config format or the `apply`/`extract`
pipeline; both modules are thin, read-only-friendly wrappers around what already exists.

## `kicadspoke.explore` — read-only querying

Grew out of a recurring pattern: answering "which components have Role=X", "what net is this
pad on", "which sheet instance (`Channel_0`/`Channel_1`/...) is this footprint under" by writing
a new throwaway script every time. `Board.select()` replaces that with one reusable call.

```python
from kicadspoke.explore import Board

board = Board.connect(schematic_dir="../test_boards/3CH-AWG-TIA")

board.select(role="AD_DAC").show()
# ref   role    cluster  sheet      nets
# ----  ------  -------  ---------  ----
# IC2   AD_DAC  -        Channel_0  ...
# IC3   AD_DAC  -        Channel_1  ...
# IC4   AD_DAC  -        Channel_2  ...

# same ambiguity that caused a real fatal in apply: role repeats twice per
# channel — select() shows it up front instead of failing mid-run.
board.select(role="R_TERM_P", sheet="Channel_0").show()
# ref   role      cluster  sheet      nets
# ----  --------  -------  ---------  ----
# R33   R_TERM_P  -        Channel_0  ...
# R39   R_TERM_P  -        Channel_0  ...

# escape hatch: .fp is the raw FootprintInstance, for anything not covered here
comp = board.select(ref="IC2")[0]
comp.nets            # {'21': '/Channel_0/DAC/DAC_OUT_P', ...}
comp.fp.position      # raw kipy object
```

`select()` filters (all optional, AND-combined):

| Filter | Match |
|---|---|
| `ref` | exact refdes |
| `role` | exact `Role` field value |
| `cluster` | **segment-prefix** — same as the real `anchor_cluster` resolver (`Channel_1` matches `Channel_1/1V2_PLL`, not `Channel_10`) |
| `sheet` | membership in the footprint's resolved sheet-instance chain |
| `net` | any pad on this net |

`Board` is a **stable snapshot**, taken at `connect()`/`refresh()` — it never re-fetches on its
own. Call `board.refresh()` after any board change (a manual edit in KiCad, or a scripted
`apply_config()` run) before trusting the next `select()`.

## `kicadspoke.author` — coding placement instead of copy-pasting YAML

Per-channel `clone_placements` written by hand are exactly where copy-paste mistakes creep in
(wrong `nets:` key, duplicate `anchor_pad:`, wrong `anchor_sheet`) — a `for` loop can't make
those. `ClonePlacement`/`Rule` (`kicadspoke.config`) are plain dataclasses; build them directly:

```python
from kicadspoke.config import ClonePlacement

clones = [
    ClonePlacement(
        name=f"channel_{i}_ad9707", role="AD_DAC",
        anchor_role="FPGA", anchor_sheet=ch,
        nets={"AD_DAC": "/Channel_{channel}/DAC/DAC_OUT_P"},
        params={"channel": i},
        origin_x_mm=0.0, origin_y_mm=25.0 - 25.0 * i, rotation_deg=270.0 - 90.0 * i,
    )
    for i, ch in enumerate(["Channel_0", "Channel_1", "Channel_2"])
]
```

**Option (a) — straight into `apply`:**

```python
from kicadspoke.config import Config, load_config
from kicadspoke.author import apply_config

cfg = load_config("profiles/3ch-awg-tia.yaml")   # or build a Config() from scratch
cfg.clone_placements.extend(clones)

apply_config(cfg, "profiles/3ch-awg-tia.yaml", dry_run=True)
```

`config_path` (second argument) is **not cosmetic** — when `cfg.registry_path`/
`cfg.track_registry_path` aren't set, the registries that make repeated `apply` runs
idempotent (no duplicate vias/tracks) are derived from it (`<config_path>.registry.json` next to
it). Point it at the real profile path you're extending, or set `cfg.registry_path`/
`cfg.track_registry_path` explicitly — never pass a throwaway placeholder.

**Option (b) — generate YAML** (keeps the config diffable/reviewable in git, Python only used at
authoring time):

```python
from kicadspoke.author import dump_clone_placements

dump_clone_placements(clones, "profiles/subsystems/dac_channels.yaml")
```

Then reference it the normal way:

```yaml
include:
  - subsystems/dac_channels.yaml
```

Both options can be combined — generate the YAML file with (b), then let normal `apply` runs
pick it up via `include:`, without ever calling `apply_config()` directly.
