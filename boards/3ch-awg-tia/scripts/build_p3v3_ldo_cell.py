#!/usr/bin/env python3
"""
boards/3ch-awg-tia/scripts/build_p3v3_ldo_cell.py

Phase 5 (see techdocs/handoff/handoff_2026_08_01_phase5_plan.md): turns the
three independently-anchored clone_placements in
boards/3ch-awg-tia/profiles/p3v3_ldo.yaml (3v3_ldo / 3v3_led_spoke /
3v3_ldo_out_pi_filter) into ONE recursive composite Cell
(p3v3_ldo_composite) — the concrete example named in
handoff_2026_07_31_consolidated.md §4.1.

Why a script and not hand-written YAML: CellPlacement (the nested-inside-a-
Cell type) has no anchor_role/anchor_pad/anchor_cluster at all by design —
only a literal xy relative to the PARENT cell's own local (0,0). Today's
three placements are anchored to specific pads on specific components
(LDO_3V3 pad 2, C_OUT_BYPASS pad 1 in cluster Pi_Filter_Out_3V3) — real
footprint geometry (component-centre-to-pad offset) that lives in the
.kicad_pcb/footprint library, not in any of these YAML files, and isn't
derivable by arithmetic on the numbers already there. This script measures
it against the LIVE board instead of guessing — same principle
dac_channels.py used for its Channel_1/2 rotation offsets (real
kipy.geometry.Vector2.rotate() math against measured positions, not
hand-guessed numbers).

PRECONDITION: boards/3ch-awg-tia/profiles/power.yaml's current
p3v3_ldo.yaml (the three independent clone_placements) must already be
*applied* to the live board — this script only reads, it does not place
anything. If LDO_3V3 / C_OUT_BYPASS(Pi_Filter_Out_3V3) aren't on the board
yet, apply the current p3v3_ldo.yaml first.

What it does (read-only, never mutates the board):
  1. Connects via kicadstamp.explore.Board (same read-only facade the
     diagnostics scripts use).
  2. Finds LDO_3V3's own placement -- ldo_3v3.yaml places that component at
     local offset (0,0), so its live board position IS the p3v3_ldo cell's
     own origin.
  3. Finds the live pad positions this board's two other placements are
     actually anchored on today (LDO_3V3 pad 2; C_OUT_BYPASS pad 1 in
     cluster Pi_Filter_Out_3V3).
  4. Subtracts the origin (and adds back today's xy: shift, unchanged) to
     get each nested placement's xy relative to the composite's own (0,0).
     This assumes rotation_deg=0 on all three of today's placements (true
     right now, verified against p3v3_ldo.yaml) -- xy: is a flat shift, not
     auto-rotated (see ClonePlacement docstring in config/models.py), so a
     plain subtraction is valid only as long as that stays true. If any of
     the three placements ever gets a non-zero rotation_deg, this script's
     math needs to compose that rotation too, not just subtract.
  5. Writes boards/3ch-awg-tia/profiles/templates/p3v3_ldo_composite.yaml
     (a Cell definition, {name: {clone_placements: [...]}} shape, same
     format as any other file in template_files:).

What it does NOT do: it does not touch profiles/p3v3_ldo.yaml itself. Once
the generated file's numbers have been sanity-checked (e.g. via
kicadstamp_cli.py apply --dry-run --verbose against
boards/3ch-awg-tia/profiles/power.yaml after wiring the new cell in),
rewrite p3v3_ldo.yaml by hand to a single clone_placement:
    - name: 3v3_ldo_system
      retired: false
      skip: false
      cell: p3v3_ldo_composite
      anchor_point: p3v3_ldo_origin   # see profiles/points.yaml
      xy: [-50.0, 35.0]
and add "templates/p3v3_ldo_composite.yaml" to power.yaml's template_files:.

Run: python boards/3ch-awg-tia/scripts/build_p3v3_ldo_cell.py
     [--config boards/3ch-awg-tia/profiles/power.yaml] [--timeout-ms 20000]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from kicadstamp.author import dump_template
from kicadstamp.explore import Board
from kicadstamp.utils.units import MM

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE.parent / "profiles" / "power.yaml"
OUTPUT = HERE.parent / "profiles" / "templates" / "p3v3_ldo_composite.yaml"

# Static config semantics carried over verbatim from today's three
# independent clone_placements in profiles/p3v3_ldo.yaml -- not board
# geometry, safe to hardcode.
LDO_REG_NETS = {"LDO_1V2": "+3V3_DIRTY"}
LDO_REG_PARAMS = {"PWR_IN": "+5V", "PWR_OUT": "+3V3_DIRTY"}
LED_SPOKE_PARAMS = {"PWR_IN": "+3V3", "PWR_OUT": "/Power/+3V3_LED"}
LDO_OUT_PI_FILTER_PARAMS = {"PWR_IN": "+3V3_DIRTY", "PWR_OUT": "+3V3"}

# Today's post-anchor flat shifts (profiles/p3v3_ldo.yaml's xy: on
# 3v3_led_spoke / 3v3_ldo_out_pi_filter) -- unchanged by this migration,
# only WHAT they're relative to changes (a live-resolved anchor pad ->
# the composite cell's own local origin).
LED_SPOKE_SHIFT_MM = (-2.0, 5.0)
LDO_OUT_PI_FILTER_SHIFT_MM = (12.0, 0.0)


def _one(board: Board, *, role: str, cluster=None):
    sel = board.select(role=role, cluster=cluster)
    if len(sel) != 1:
        raise SystemExit(
            f"expected exactly one component with role={role!r} cluster={cluster!r} "
            f"on the live board, found {len(sel)} -- is profiles/p3v3_ldo.yaml's "
            f"current (pre-migration) version applied to this board yet?"
        )
    return sel[0].fp


def _pad_position_mm(board: Board, fp, pad_number: str):
    pads = board.adapter.get_footprint_pads(fp)
    matches = [p for p in pads if p.number == pad_number]
    if not matches:
        raise SystemExit(f"{board._ref(fp)} has no pad {pad_number!r}")
    pos = matches[0].position
    return pos.x / MM, pos.y / MM


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                         help="profile path, for schematic_dir resolution (default: power.yaml)")
    parser.add_argument("--timeout-ms", type=int, default=20000)
    args = parser.parse_args()

    board = Board.connect(timeout_ms=args.timeout_ms, config_path=args.config)

    ldo_fp = _one(board, role="LDO_3V3")
    # LDO_3V3's own placement origin -- ldo_3v3.yaml places this component
    # at local offset (0, 0), so its live centre IS the cell's own origin
    # (not a pad -- use the footprint position itself, not a pad lookup).
    origin_x_mm, origin_y_mm = ldo_fp.position.x / MM, ldo_fp.position.y / MM

    pi_filter_pad_x_mm, pi_filter_pad_y_mm = _pad_position_mm(board, ldo_fp, "2")
    led_anchor_fp = _one(board, role="C_OUT_BYPASS", cluster="Pi_Filter_Out_3V3")
    led_pad_x_mm, led_pad_y_mm = _pad_position_mm(board, led_anchor_fp, "1")

    pi_filter_xy = (
        round(pi_filter_pad_x_mm - origin_x_mm + LDO_OUT_PI_FILTER_SHIFT_MM[0], 4),
        round(pi_filter_pad_y_mm - origin_y_mm + LDO_OUT_PI_FILTER_SHIFT_MM[1], 4),
    )
    led_xy = (
        round(led_pad_x_mm - origin_x_mm + LED_SPOKE_SHIFT_MM[0], 4),
        round(led_pad_y_mm - origin_y_mm + LED_SPOKE_SHIFT_MM[1], 4),
    )

    template_dict = {
        "p3v3_ldo_composite": {
            "clone_placements": [
                {
                    "name": "ldo_reg",
                    "cell": "p3v3_ldo",
                    "xy": [0.0, 0.0],
                    "nets": LDO_REG_NETS,
                    "params": LDO_REG_PARAMS,
                },
                {
                    "name": "led_spoke",
                    "cell": "led_spoke",
                    "xy": list(led_xy),
                    "params": LED_SPOKE_PARAMS,
                },
                {
                    "name": "ldo_out_pi_filter",
                    "cell": "ldo_out_pi_filter",
                    "xy": list(pi_filter_xy),
                    "params": LDO_OUT_PI_FILTER_PARAMS,
                },
            ]
        }
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    dump_template(template_dict, str(OUTPUT))
    print(f"wrote {OUTPUT}")
    print(f"  ldo_reg:           xy=[0.0, 0.0]  (this is the composite's own origin)")
    print(f"  led_spoke:         xy={list(led_xy)}")
    print(f"  ldo_out_pi_filter: xy={list(pi_filter_xy)}")
    print()
    print("Next: add \"templates/p3v3_ldo_composite.yaml\" to power.yaml's template_files:, "
          "then sanity-check with apply --dry-run --verbose before rewriting "
          "profiles/p3v3_ldo.yaml by hand (see this script's own docstring).")


if __name__ == "__main__":
    main()
