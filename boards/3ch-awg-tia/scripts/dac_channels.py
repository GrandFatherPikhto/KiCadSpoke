#!/usr/bin/env python3
"""
boards/3ch-awg-tia/scripts/dac_channels.py — generates clone_placements for
the 3-channel DAC section (AD9707 + its immediate passives), reproducing
what's already live and verified in profiles/3ch-awg-tia.yaml, via
kicadspoke.author instead of hand-written YAML (see
docs/board_coding.md for the walkthrough this mirrors).

AD_DAC itself is placed on all 3 channels — a real per-channel lookup table,
NOT a formula (each channel's DAC sits on a different side of the FPGA, see
AD_DAC_LAYOUT below). The immediate passives (R_TERM_P/N, C_DAC_REFIO,
R_DAC_FS_ADJ) use the same lookup-table style, PASSIVE_LAYOUT: Channel_0's
row per role is the hand-verified baseline (see profiles/3ch-awg-tia.yaml);
Channel_1/2 rows were derived by rotating that flat offset by the delta
between each channel's AD_DAC rotation_deg and Channel_0's, using
kipy.geometry.Vector2.rotate() — the SAME rotation the placement engine
itself applies to template geometry (kicadspoke/geometry/spoke_layout.py's
rotate_local_offset) — NOT hand-guessed numbers. This is needed because
origin_x_mm/origin_y_mm is a FLAT shift from the anchor, NOT auto-rotated
by the engine (see ClonePlacement's docstring in kicadspoke/config/models.py
and clone_geometry.py:109-113): reusing Channel_0's numbers verbatim on a
differently-rotated channel would silently misplace the passive.
PASSIVE_LAYOUT's Channel_1/2 rows (and OP_AMPS below, same idea) have been
visually verified in KiCad after applying (2026-07-28) — the
rotation-of-baseline approach checked out.

Unlike hand-written YAML (profiles/3ch-awg-tia.yaml), this script does NOT
use ClonePlacement.params/{channel}-in-nets/anchor_sheet placeholder
substitution — that mechanism exists because YAML has no string
interpolation of its own. Here `channel` is already a concrete Python loop
variable, so nets/anchor_sheet are resolved directly (f-string/.format())
at generation time; the dumped YAML carries plain literal values.

Run: python boards/3ch-awg-tia/scripts/dac_channels.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from kicadspoke.author import cli_main
from kicadspoke.config import ClonePlacement

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parent / "generated" / "dac_channels.yaml"

# (origin_x_mm, origin_y_mm, rotation_deg) per channel — NOT a formula, each
# DAC sits on a different side of the FPGA.
AD_DAC_LAYOUT = {
    0: (0.0, 25.0, 270.0),
    1: (25.0, 0.0, 0.0),
    2: (0.0, -25.0, 90.0),
}

# (origin_x_mm, origin_y_mm, rotation_deg) per channel, per passive role —
# see module docstring for how Channel_1/2 rows were derived.
PASSIVE_LAYOUT = {
    "R_TERM_P":     [(0.4, 3.0, 270.0), (3.0, -0.4, 0.0), (-0.4, -3.0, 90.0)],
    "R_TERM_N":     [(-0.4, 3.0, 270.0), (3.0, 0.4, 0.0), (0.4, -3.0, 90.0)],
    "C_DAC_REFIO":  [(0.7, 3.0, 270.0), (3.0, -0.7, 0.0), (-0.7, -3.0, 90.0)],
    "R_DAC_FS_ADJ": [(1.5, 3.0, 270.0), (3.0, -1.5, 0.0), (-1.5, -3.0, 90.0)],
}

# (origin_x_mm, origin_y_mm, rotation_deg) per channel — same
# rotation-of-baseline idea as PASSIVE_LAYOUT above.
OP_AMPS = [
        (0.0, 10.0, 180.0),
        (10.0, 0.0, 270.0),
        (0.0, -10.0, 0.0)
    ]

# anchor_pad (on AD_DAC) and net template per passive role.
PASSIVE_PADS = {
    "R_TERM_P": ("21", "/Channel_{channel}/DAC/DAC_OUT_P"),
    "R_TERM_N": ("20", "/Channel_{channel}/DAC/DAC_OUT_N"),
    "C_DAC_REFIO": ("23", "/Channel_{channel}/DAC/DAC_REFIO"),
    "R_DAC_FS_ADJ": ("24", "/Channel_{channel}/DAC/DAC_FS_ADJ"),
}


def build() -> list:
    clones = []

    for channel, (x, y, rot) in AD_DAC_LAYOUT.items():
        clones.append(ClonePlacement(
            name=f"channel_{channel}_ad9707", role="AD_DAC",
            anchor_role="FPGA", anchor_sheet=f"Channel_{channel}",
            nets={"AD_DAC": f"/Channel_{channel}/DAC/DAC_OUT_P"},
            origin_x_mm=x, origin_y_mm=y, rotation_deg=rot,
        ))

    for role, offsets in PASSIVE_LAYOUT.items():
        anchor_pad, net_template = PASSIVE_PADS[role]
        for channel, (x, y, rot) in enumerate(offsets):
            channel_name = f"Channel_{channel}"
            clones.append(ClonePlacement(
                name=f"channel_{channel}_{role.lower()}", role=role,
                anchor_role="AD_DAC", anchor_sheet=channel_name, anchor_pad=anchor_pad,
                nets={role: net_template.format(channel=channel)},
                origin_x_mm=x, origin_y_mm=y, rotation_deg=rot,
            ))

    # OP_AMP: anchored on AD_DAC, not on R_TERM_P — R_TERM_P repeats twice per
    # channel (DAC-side termination vs. amp-output termination, no Cluster
    # tag to tell them apart), OP_AMP itself is unique per channel.
    for channel, coords in enumerate(OP_AMPS):
        clones.append(ClonePlacement(
            name=f"channel_{channel}_op_amp", role="OP_AMP",
            anchor_role="AD_DAC", anchor_sheet=f"Channel_{channel}",
            nets={"OP_AMP": f"/Channel_{channel}/OpAmp/OA_IN_P"},
            origin_x_mm=coords[0], origin_y_mm=coords[1], rotation_deg=coords[2], enabled=True, active=True
        ))

    return clones


if __name__ == "__main__":
    cli_main(build, str(OUTPUT), str(HERE.parent / "profiles/dac_channels.yaml"), description=__doc__)
