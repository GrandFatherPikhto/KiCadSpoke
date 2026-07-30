# kicadspoke/undo.py

import json
import logging
from pathlib import Path
from kipy.board_types import BoardLayer
from kipy.geometry import Vector2, Angle
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.utils.units import MM
from kicadspoke.i18n import _

logger = logging.getLogger(__name__)


def undo_last_operation(json_path: Path) -> bool:
    """Undoes the operation described in the JSON file."""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()

    # 1. Restore moved components
    for item in data.get('moves', []):
        ref = item['ref']
        fp = adapter.get_footprint(ref)
        if fp is None:
            logger.warning(_("Component {ref} not found, skipping").format(ref=ref))
            continue

        # Determine original layer
        orig_layer_str = item.get('original_layer', 'F.Cu')
        if 'B.Cu' in orig_layer_str:
            orig_layer = BoardLayer.BL_B_Cu
        else:
            orig_layer = BoardLayer.BL_F_Cu

        # If current layer differs from original — flip
        if fp.layer != orig_layer:
            logger.debug(_("Restoring {ref} to layer {layer} (flip)").format(ref=ref, layer=orig_layer_str))
            adapter.flip_selected([fp])
            # After flip, re‑read the footprint
            fp = adapter.get_footprint(ref)
            if fp is None:
                continue

        # Restore position and angle
        orig_x = item['original_position']['x']
        orig_y = item['original_position']['y']
        orig_angle = item['original_angle_deg']
        fp.position = Vector2.from_xy(int(orig_x), int(orig_y))
        fp.orientation = Angle.from_degrees(orig_angle)
        adapter.update_items([fp])
        logger.debug(_("Restored {ref} to position ({x:.3f}, {y:.3f}) mm, angle {angle:.1f}°")
                     .format(ref=ref, x=orig_x/MM, y=orig_y/MM, angle=orig_angle))

    # 2. Delete created vias (by UUID)
    for via_data in data.get('created_vias', []):
        uuid_str = via_data.get('uuid')
        if uuid_str:
            if adapter.remove_by_id(uuid_str):
                logger.debug(_("Deleted via with UUID {uuid}").format(uuid=uuid_str))

    # 2b. Delete created tracks (by UUID) — tracks were not moved, so only deletion is needed
    for track_data in data.get('created_tracks', []):
        uuid_str = track_data.get('uuid')
        if uuid_str:
            if adapter.remove_by_id(uuid_str):
                logger.debug(_("Deleted track with UUID {uuid}").format(uuid=uuid_str))

    # 3. Delete the operation file (to prevent undoing twice)
    try:
        json_path.unlink()
        logger.debug(_("File {name} deleted.").format(name=json_path.name))
    except Exception as e:
        logger.warning(_("Failed to delete file {name}: {e}").format(name=json_path.name, e=e))

    return True