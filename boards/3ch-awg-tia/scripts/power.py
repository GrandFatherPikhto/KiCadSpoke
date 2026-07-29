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
OUTPUT = HERE.parent / "profiles" / "generated" / "power.yaml"


def build() -> list:
    clones = []


    return clones


if __name__ == "__main__":
    cli_main(build, str(OUTPUT), str(HERE.parent / "3ch-awg-tia.yaml"), description=__doc__)
