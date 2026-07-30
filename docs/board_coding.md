# Board coding: a worked walkthrough with `explore`/`author`

This is a narrative tutorial, not an API reference (that's [docs/scripting.md](scripting.md)) —
a step-by-step run through placing a repeated 3-channel subsystem entirely from Python, mirroring
a real session on the `3CH-AWG-TIA` board.

## The starting point

The board has three identical DAC channels (`AD9707`), each on its own instance of a reused
hierarchical sheet (`channel.kicad_sch`, used 3× as `Channel_0`/`Channel_1`/`Channel_2`). Around
each DAC sits a small cluster of passives: termination resistors (`R_TERM_P`/`R_TERM_N`), a
reference cap (`C_DAC_REFIO`), a full-scale-adjust resistor (`R_DAC_FS_ADJ`), and an op-amp
(`OP_AMP`). Writing one `clone_placement` block per component per channel by hand is exactly
where copy-paste mistakes live — this walkthrough builds them from a loop instead.

## Step 1 — look before you leap

Before writing any placement config, use `explore` to see what you're actually dealing with —
don't guess at Role/Cluster/net names or assume a Role is unique:

```python
from kicadstamp.explore import Board

board = Board.connect(config_path="profiles/3ch-awg-tia.yaml",
                       schematic_dir="../test_boards/3CH-AWG-TIA")

board.select(role="AD_DAC").show()
# ref   role    cluster  sheet          nets
# ----  ------  -------  -------------  ----
# IC2   AD_DAC  -        Channel_0/DAC  ...
# IC3   AD_DAC  -        Channel_1/DAC  ...
# IC4   AD_DAC  -        Channel_2/DAC  ...
```

Good — one `AD_DAC` per channel, distinguishable by `sheet`. Now check the termination resistor:

```python
board.select(role="R_TERM_P", sheet="Channel_0").show()
# ref  role      cluster  sheet            nets
# ---  --------  -------  ---------------  ----
# R33  R_TERM_P  -        Channel_0/DAC    1=.../DAC_OUT_P, 2=.../OA_IN_P
# R39  R_TERM_P  -        Channel_0/OpAmp  1=.../OA_OUT_P, 2=.../PA_IN_P
```

Two candidates on one channel — the same Role is reused for two different physical roles (DAC-side
termination vs. amp-output termination). This is exactly the kind of ambiguity that would
otherwise only surface as a fatal error mid-`apply`. Seeing it here means it can be designed
around from the start: anchor on `AD_DAC` (already unique per channel) for the DAC-side one,
and pick the specific ref or a schematic `Cluster` tag for the amp-output one.

## Step 2 — express the repetition as a loop

Once the shape is confirmed, the per-channel components become a plain Python loop instead of
three hand-written, copy-pasted YAML blocks:

```python
from kicadstamp.config import ClonePlacement

channels = ["Channel_0", "Channel_1", "Channel_2"]

clones = []
for i, ch in enumerate(channels):
    clones.append(ClonePlacement(
        name=f"channel_{i}_ad9707", role="AD_DAC",
        anchor_role="FPGA", anchor_sheet=ch,
        nets={"AD_DAC": "/Channel_{channel}/DAC/DAC_OUT_P"},
        params={"channel": i},
        origin_x_mm=0.0, origin_y_mm=25.0 - 25.0 * i, rotation_deg=270.0 - 90.0 * i,
    ))
    clones.append(ClonePlacement(
        name=f"channel_{i}_r_term_p", role="R_TERM_P",
        anchor_role="AD_DAC", anchor_sheet="Channel_{channel}", anchor_pad="21",
        nets={"R_TERM_P": "/Channel_{channel}/DAC/DAC_OUT_P"},
        params={"channel": i},
        origin_x_mm=0.4, origin_y_mm=3.0, rotation_deg=270.0,
    ))
    # ... R_TERM_N, C_DAC_REFIO, R_DAC_FS_ADJ, OP_AMP follow the same shape
```

Note `anchor_sheet="Channel_{channel}"` — a `{placeholder}` resolved from `params`, same mechanism
as `nets`/`net_template` (see `resolve_placeholder` in `kicadstamp/net_resolution.py`). This is
what makes the per-instance disambiguation from Step 1 actually work across three loop iterations
instead of three manually-typed sheet names.

A `for` loop physically cannot make the mistakes that come from copy-pasting three similar blocks
by hand: a wrong `nets:` key, a duplicated `anchor_pad:` line, a sheet name copied from the wrong
neighbour — all three were real bugs hit while writing this exact config by hand.

## Step 3 — try it

```python
from kicadstamp.config import load_config
from kicadstamp.author import apply_config

cfg = load_config("profiles/3ch-awg-tia.yaml")
cfg.clone_placements.extend(clones)

apply_config(cfg, "profiles/3ch-awg-tia.yaml", dry_run=True)
```

Re-run `board.refresh()` and `board.select(...)` afterwards (real `apply`, not `--dry-run`) to
confirm the result with the same tool used to investigate the ambiguity in Step 1 — the loop back
to `explore` closes the "did it actually do what I meant" question without opening KiCad.

## Step 4 — decide how it should live in git

Two options, not mutually exclusive:

- **Keep it as a script** you re-run whenever the subsystem needs regenerating — good while still
  iterating on offsets/params.
- **Freeze it to YAML** once it's right, so the config that actually ships is plain, diffable,
  reviewable text, and the Python loop was only a drafting tool:

  ```python
  from kicadstamp.author import dump_clone_placements
  dump_clone_placements(clones, "profiles/subsystems/dac_channels.yaml")
  ```

  ```yaml
  # profiles/3ch-awg-tia.yaml
  include:
    - subsystems/dac_channels.yaml
  ```

## One thing to not get wrong

`apply_config(cfg, config_path, ...)` — `config_path` decides where the via/track registries live
(`registry_path_for_config()` in `kicadstamp/registry.py`, unless `cfg.registry_path` is set
explicitly). Pass the SAME `config_path` every time you re-run the same script, or the registry
will treat each run as a fresh board and start duplicating vias/tracks instead of reconciling
against what's already there — see [docs/scripting.md](scripting.md) for the full explanation.
