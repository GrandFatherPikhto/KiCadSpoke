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
R_DAC_FS_ADJ, OP_AMP) are only reproduced for Channel_0 here — their
origin_x_mm/origin_y_mm is a FLAT shift from the anchor, NOT rotated to match
the anchor's own rotation_deg (see ClonePlacement's docstring in
kicadspoke/config/models.py), so replicating them to Channel_1/2 needs each
offset recomputed for that channel's DAC orientation — not worked out yet,
left for a follow-up script (see
techdocs/handoff/handoff_2026_07_28_pcb_api.md, "что дальше").

Run: python boards/3ch-awg-tia/scripts/dac_channels.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from kicadspoke.author import dump_clone_placements
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

# Channel_0-only immediate passives around the DAC — see module docstring
# for why 1/2 aren't replicated here yet.
PASSIVES = [
    dict(name="channel_0_r_term_p", role="R_TERM_P", anchor_pad="21",
         net="/Channel_{channel}/DAC/DAC_OUT_P",
         origin_x_mm=0.4, origin_y_mm=3.0, rotation_deg=270.0),
    dict(name="channel_0_r_term_n", role="R_TERM_N", anchor_pad="20",
         net="/Channel_{channel}/DAC/DAC_OUT_N",
         origin_x_mm=-0.4, origin_y_mm=3.0, rotation_deg=270.0),
    dict(name="channel_0_c_dac_refio", role="C_DAC_REFIO", anchor_pad="23",
         net="/Channel_{channel}/DAC/DAC_REFIO",
         origin_x_mm=0.7, origin_y_mm=3.0, rotation_deg=270.0),
    dict(name="channel_0_r_dac_fs_adj", role="R_DAC_FS_ADJ", anchor_pad="24",
         net="/Channel_{channel}/DAC/DAC_FS_ADJ",
         origin_x_mm=1.5, origin_y_mm=3.0, rotation_deg=270.0),
]


def build() -> list:
    clones = []

    for channel, (x, y, rot) in AD_DAC_LAYOUT.items():
        clones.append(ClonePlacement(
            name=f"channel_{channel}_ad9707", role="AD_DAC",
            anchor_role="FPGA", anchor_sheet=f"Channel_{channel}",
            nets={"AD_DAC": "/Channel_{channel}/DAC/DAC_OUT_P"},
            params={"channel": channel},
            origin_x_mm=x, origin_y_mm=y, rotation_deg=rot,
        ))

    for p in PASSIVES:
        clones.append(ClonePlacement(
            name=p["name"], role=p["role"],
            # anchor_sheet supports {placeholder} substitution from params
            # (see resolve_placeholder in kicadspoke/net_resolution.py) —
            # 'Channel_{channel}' resolves per-instance, not a literal.
            anchor_role="AD_DAC", anchor_sheet="Channel_{channel}", anchor_pad=p["anchor_pad"],
            nets={p["role"]: p["net"]}, params={"channel": 0},
            origin_x_mm=p["origin_x_mm"], origin_y_mm=p["origin_y_mm"],
            rotation_deg=p["rotation_deg"],
        ))

    # OP_AMP: anchored on AD_DAC, not on R_TERM_P — R_TERM_P repeats twice per
    # channel (DAC-side termination vs. amp-output termination, no Cluster
    # tag to tell them apart), OP_AMP itself is unique per channel.
    clones.append(ClonePlacement(
        name="channel_0_op_amp", role="OP_AMP",
        anchor_role="AD_DAC", anchor_sheet="Channel_{channel}",
        nets={"OP_AMP": "/Channel_{channel}/OpAmp/OA_IN_P"}, params={"channel": 0},
        origin_x_mm=0.0, origin_y_mm=10.0, rotation_deg=180.0,
    ))

    return clones


if __name__ == "__main__":
    clones = build()
    OUTPUT.parent.mkdir(exist_ok=True)
    dump_clone_placements(clones, str(OUTPUT))
    print(f"wrote {len(clones)} clone_placements to {OUTPUT}")
