#!/usr/bin/env python3
"""
boards/3ch-awg-tia/scripts/dac_pi_filter.py — places the dac_pi_filter
template (extracted via `extract --name dac_pi_filter`, see
boards/3ch-awg-tia/templates/templates.yaml) anchored on AD_DAC's pad 11,
Channel_0 only for now.

The template has two net_template placeholders, '{PWR_IN}' and '{PWR_OUT}'
(the extractor's own --net-template matching missed both originally, since
it needs an EXACT string match and the real net has a hierarchical prefix,
/Channel_0/DAC/+3V3_CLKVDD, not the bare +3V3_CLKVDD passed to
--net-template — templates.yaml was hand-fixed afterwards to use the
placeholders directly). Both need params={"PWR_IN": ..., "PWR_OUT": ...}
at clone time to resolve — this template is Channel_0-only as extracted
either way (the params below are literal for Channel_0); reusing it on
other channels needs a params={"channel": N}-driven net_template, not done
here.

Run: python boards/3ch-awg-tia/scripts/dac_pi_filter.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from kicadspoke.author import cli_main
from kicadspoke.config import ClonePlacement

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parent / "generated" / "dac_pi_filter.yaml"

DAC_PWR_CLK_VDD = [
    (-2.0, 0.0, 0.0),
    (0.0, 2.0, 90.0),
    (2.0, 0.0, 180.0)
]

DAC_PWR_AVDD = [
    (-2.0, 0.0, 0.0),
    (0.0, 2.0, 90.0),
    (2.0, 0.0, 180.0)
]

def build() -> list:
    clones = []

    for channel, dac_pwr in enumerate(DAC_PWR_CLK_VDD):
        clones.append(ClonePlacement(
            name=f"Channel_{channel}_DAC_Pi_Filter_Clk_Vdd", template="dac_pi_filter",
            anchor_cluster="DAC_Pi_Filter_Clk_Vdd",
            anchor_role="AD_DAC", anchor_sheet=f"Channel_{channel}", anchor_pad="11",
            params={"PWR_IN": "+3V3", "PWR_OUT": f"/Channel_{channel}/DAC/+3V3_CLKVDD"},
            origin_x_mm=dac_pwr[0], origin_y_mm=dac_pwr[1], rotation_deg=dac_pwr[2],
        ))

    for channel, dac_pwr in enumerate(DAC_PWR_AVDD):
        clones.append(ClonePlacement(
            name=f"Channel_{channel}_DAC_Pi_Filter_Add", template="dac_pi_filter",
            anchor_cluster="DAC_Pi_Filter_Avdd",
            anchor_role="AD_DAC", anchor_sheet=f"Channel_{channel}", anchor_pad="18",
            params={"PWR_IN": "+3V3", "PWR_OUT": f"/Channel_{channel}/DAC/+3V3_AVDD"},
            origin_x_mm=dac_pwr[0], origin_y_mm=dac_pwr[1], rotation_deg=dac_pwr[2],
        ))

    return clones


if __name__ == "__main__":
    cli_main(build, str(OUTPUT), str(HERE.parent / "3ch-awg-tia.yaml"), description=__doc__)
