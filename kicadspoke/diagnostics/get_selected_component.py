#!/usr/bin/env python3
"""
diagnostics/get_selected_component.py — prints detailed information
about components selected on the board (refdes, value, footprint, position,
angle, size, pads, nets, Role field).

Run: select components in KiCad and execute:
    python -m kicadspoke.diagnostics.get_selected_component
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from kipy.board_types import FootprintInstance
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.utils.units import MM
from kicadspoke.i18n import _

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()

    # Get selected items (expanding groups)
    items = adapter.get_selected_items()
    footprints = [i for i in items if isinstance(i, FootprintInstance)]

    if not footprints:
        print(_("Nothing selected in KiCad (or selected items are not components)."))
        return

    # Get bounding boxes for all footprints in one batch request
    bboxes = adapter.get_bounding_boxes(footprints)

    print(_("Selected components: {count}\n").format(count=len(footprints)))
    print("=" * 100)

    for fp, bbox in zip(footprints, bboxes):
        ref = fp.reference_field.text.value
        val = fp.value_field.text.value
        fp_name = str(fp.definition.id)
        x = fp.position.x / MM
        y = fp.position.y / MM
        angle = fp.orientation.degrees
        role = adapter.get_field_value(fp, "Role") or _("(not set)")

        if bbox:
            w = bbox.size.x / MM
            h = bbox.size.y / MM
            size_str = _("{w:.3f} x {h:.3f} mm").format(w=w, h=h)
        else:
            size_str = _("? (bbox unavailable)")

        print(_("[{ref}]").format(ref=ref))
        print(_("  Value:        {val}").format(val=val))
        print(_("  Footprint:    {fp}").format(fp=fp_name))
        print(_("  Position:     ({x:.3f}, {y:.3f}) mm").format(x=x, y=y))
        print(_("  Angle:        {angle:.1f}°").format(angle=angle))
        print(_("  Size:         {size}").format(size=size_str))
        print(_("  Role:         {role}").format(role=role))

        # Pads
        pads = adapter.get_footprint_pads(fp)
        if pads:
            print(_("  Pads:"))
            for pad in pads:
                pnum = pad.number
                net = pad.net.name if pad.net else _("(none)")
                px = pad.position.x / MM
                py = pad.position.y / MM
                # pad size (copper layer)
                copper = pad.padstack.copper_layers
                if copper:
                    pw = copper[0].size.x / MM
                    ph = copper[0].size.y / MM
                    psize = _("{pw:.2f} x {ph:.2f} mm").format(pw=pw, ph=ph)
                else:
                    psize = _("?x?")
                print(_("    {pnum}: net={net:<15} pos=({px:.3f}, {py:.3f}) mm size={psize}")
                      .format(pnum=pnum, net=net, px=px, py=py, psize=psize))
        print()

    print("=" * 100)


if __name__ == "__main__":
    main()