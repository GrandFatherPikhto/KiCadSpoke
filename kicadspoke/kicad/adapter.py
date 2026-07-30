# kicadspoke/kicad/adapter.py

import time
import logging
from typing import List, Optional, Any
import kipy
from kipy.board_types import FootprintInstance, Zone, Net, Via, ViaType, Track, BoardLayer, Pad, Field, Group
from kipy.geometry import Vector2, Box2, Angle

from .interfaces import IBoardAdapter
from ..exceptions import BoardNotFoundError, ComponentNotFoundError
from ..utils.units import MM
from ..constants import DEFAULT_TIMEOUT_MS
from ..i18n import _

logger = logging.getLogger(__name__)


class KiCadBoardAdapter(IBoardAdapter):
    def __init__(self, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        logger.debug(_("Initialising KiCadBoardAdapter with timeout {timeout} ms").format(timeout=timeout_ms))
        logger.debug(_("Creating kipy.KiCad instance..."))
        self._kicad = kipy.KiCad(timeout_ms=timeout_ms)
        logger.debug(_("kipy.KiCad instance created"))
        self._board = None
        self._write_risk_checked = False
        self._footprints_cache: Optional[List[FootprintInstance]] = None
        # Settable by the caller (see kicadspoke_cli.py's --no-selection) —
        # makes get_selected_items() always report "nothing selected",
        # regardless of what's actually highlighted in the PCB editor GUI.
        # ClonePlacement's "by selection" mode (role:/template: without nets/
        # params) and the selection-narrowing step in _narrow_ambiguous_
        # candidates/resolve_footprint_by_role both read the selection as
        # part of a normal resolution cascade — a stray leftover mouse
        # selection from earlier browsing then fatals or silently changes
        # which candidate wins, with no indication it came from the GUI, not
        # the config. This flag lets a whole apply run opt out of that input.
        self.ignore_selection = False

    def refresh_board(self):
        logger.debug(_("Refreshing board from KiCad"))
        self._board = self._kicad.get_board()
        if self._board is None:
            raise BoardNotFoundError(_("Failed to obtain board from KiCad"))
        self._footprints_cache = None
        logger.info(_("Board obtained"))

    # --- Search ---
    def get_footprint(self, ref: str) -> Optional[FootprintInstance]:
        for fp in self.get_footprints():
            if fp.reference_field.text.value == ref:
                logger.debug(_("Found footprint {ref}").format(ref=ref))
                return fp
        logger.debug(_("Footprint {ref} not found").format(ref=ref))
        return None

    def get_footprints(self) -> List[FootprintInstance]:
        """
        Cached per board generation (cleared by refresh_board()). Anchor/role
        resolution (clone_role_resolver.py), ComponentPool, dependency_order.py
        (which resolves every rule/clone_placement's anchor TWICE — once to
        build the dependency graph, once again to actually plan it) and the
        collision check in move_executor.py each used to call this fresh,
        every time — a single apply run over ~20 rules/clone_placements meant
        dozens of full-board IPC round trips for data that cannot have changed
        since the last explicit refresh_board() (every place in this codebase
        that moves/creates something already calls refresh_board() before the
        next read that needs to see it — see cmd_apply's per-item loop and the
        moves -> vias -> tracks phase boundaries). Confirmed 2026-07-29 by
        reading the call graph (dependency_order.py alone doubles every
        anchor/role resolution); not re-profiled live since the redundancy is
        structural, not data-dependent.
        """
        if self._footprints_cache is None:
            self._footprints_cache = list(self._board.get_footprints())
            logger.debug(_("Retrieved {count} footprints").format(count=len(self._footprints_cache)))
        return list(self._footprints_cache)

    def get_vias(self) -> List[Via]:
        vias = list(self._board.get_vias())
        logger.debug(_("Retrieved {count} vias").format(count=len(vias)))
        return vias

    def get_tracks(self) -> List[Track]:
        tracks = list(self._board.get_tracks())
        logger.debug(_("Retrieved {count} tracks").format(count=len(tracks)))
        return tracks

    def get_selected_items(self) -> List[Any]:
        """
        Current selection in PCB editor, taking Groups into account — Group's
        .items property as received from the server is ALWAYS EMPTY (just a
        local cache wrapper); actual group members are in .proto.items (list of
        KIID). Expand groups into their real members by matching IDs against all
        footprints/vias on the board.

        self.ignore_selection short-circuits this to always report "nothing
        selected" — see its docstring in __init__.
        """
        if self.ignore_selection:
            return []
        raw_selection = list(self._board.get_selection())
        direct_items = [item for item in raw_selection if not isinstance(item, Group)]
        group_uuids = set()
        for item in raw_selection:
            if isinstance(item, Group):
                for kiid in item.proto.items:
                    group_uuids.add(str(kiid.value))

        if group_uuids:
            for fp in self.get_footprints():
                if str(fp.id.value) in group_uuids:
                    direct_items.append(fp)
            for via in self.get_vias():
                if str(via.id.value) in group_uuids:
                    direct_items.append(via)

        logger.debug(_("Selected items (including groups expanded): {count}").format(count=len(direct_items)))
        return direct_items

    def get_field_value(self, footprint: FootprintInstance, field_name: str) -> Optional[str]:
        """
        Value of a custom component field (e.g., Role for KiCadSpoke 4.0).
        IMPORTANT: texts_and_fields contains a mix of actual Field objects
        (name+text.value) and plain BoardText (silkscreen text without a field
        name at all) — filter by type, otherwise we get AttributeError on .name
        for BoardText.
        """
        for item in footprint.texts_and_fields:
            if isinstance(item, Field) and item.name == field_name:
                return item.text.value if item.text else None
        return None

    def get_footprint_pads(self, footprint: FootprintInstance) -> List[Pad]:
        """
        Returns the list of pads of this footprint. Does not go to the API
        separately — pads are already in footprint.definition.items together
        with fields/graphics; just filter by type. Moved here from planner.py
        to avoid duplication in future places (e.g., keepout building).
        """
        return [item for item in footprint.definition.items if isinstance(item, Pad)]

    def get_pad_by_number(self, footprint: FootprintInstance, pad_number: str) -> Optional[Pad]:
        """Finds a specific pad of a footprint by number (e.g., '1', '145')."""
        for pad in self.get_footprint_pads(footprint):
            if pad.number == pad_number:
                return pad
        return None

    def get_zone_by_name(self, name: str) -> Optional[Zone]:
        for z in self._board.get_zones():
            if z.name == name:
                logger.debug(_("Found zone {name}").format(name=name))
                return z
        logger.debug(_("Zone {name} not found").format(name=name))
        return None

    def get_net_by_name(self, name: str) -> Optional[Net]:
        for n in self._board.get_nets():
            if n.name == name:
                logger.debug(_("Found net {name}").format(name=name))
                return n
        logger.debug(_("Net {name} not found").format(name=name))
        return None

    def get_all_nets(self) -> List[Net]:
        nets = list(self._board.get_nets())
        logger.debug(_("Retrieved {count} nets").format(count=len(nets)))
        return nets

    # --- Bounding boxes (for collisions — see collision.py) ---
    def get_bounding_boxes(self, items) -> List[Optional[Box2]]:
        """
        Returns bounding boxes (Box2 | None) for a list of items in ONE request.
        Board.get_item_bounding_box(list) returns List[Optional[Box2]] for a
        sequence of items (for a single item it would return just Box2|None —
        so we always pass a list here).
        """
        if not items:
            return []
        result = self._board.get_item_bounding_box(list(items))
        # Defensive normalisation in case it's not a list
        if not isinstance(result, list):
            result = [result]
        return result

    # --- Transactions ---
    def begin_commit(self):
        logger.debug(_("Beginning transaction"))
        return self._board.begin_commit()

    def push_commit(self, commit, description: str):
        logger.debug(_("Committing transaction: {desc}").format(desc=description))
        self._board.push_commit(commit, description)
        logger.info(_("Transaction committed: {desc}").format(desc=description))

    def drop_commit(self, commit):
        logger.warning(_("Rolling back transaction"))
        self._board.drop_commit(commit)

    def check_write_crash_risk(self):
        """
        Check before the FIRST mutating operation: KiCad 10.0.4 may crash
        entirely on the first API write if the schematic editor is open and no
        interactive edit has been made in the session (null‑deref in
        _eeschema.dll, our report:
        https://gitlab.com/kicad/code/kicad/-/issues/24966).
        Cannot prevent the crash from the client, but can warn.
        Called once; subsequent calls are no‑op.
        """
        if self._write_risk_checked:
            return
        self._write_risk_checked = True
        try:
            from kipy.proto.common.types import DocumentType
            schematics = self._kicad.get_open_documents(DocumentType.DOCTYPE_SCHEMATIC)
        except Exception as e:
            logger.debug(_("Checking open schematics failed: {e}").format(e=e))
            return
        if schematics:
            logger.warning(
                _("Schematic editor is open: if there have been no interactive edits "
                  "in this KiCad session, the first API write may crash KiCad "
                  "(issue #24966). Workaround: move any component + Ctrl+S in "
                  "pcbnew, or close the schematic window during the run.")
            )

    def _mutating_call(self, op_name: str, fn, retries: int = 2, backoff_s: float = 1.5):
        """
        Wrapper for mutating calls: before first — check_write_crash_risk,
        on ApiError 'not ready' — retry with backoff (KiCad busy with modal
        state), on ConnectionError — clear diagnosis instead of raw stack trace:
        pipe break during write = KiCad probably crashed (see issue #24966).
        """
        self.check_write_crash_risk()
        last_exc = None
        for attempt in range(1 + retries):
            try:
                return fn()
            except kipy.errors.ApiError as e:
                if "not ready" in str(e).lower() and attempt < retries:
                    wait = backoff_s * (attempt + 1)
                    logger.warning(_("{op}: KiCad not ready to respond "
                                     "(busy/modal dialog?), retrying in {wait:.1f}s "
                                     "[{attempt}/{retries}]")
                                   .format(op=op_name, wait=wait,
                                           attempt=attempt+1, retries=retries))
                    time.sleep(wait)
                    last_exc = e
                    continue
                raise
            except kipy.errors.ConnectionError as e:
                logger.error(
                    _("{op}: connection to KiCad broke during write — "
                      "KiCad probably crashed (known crash on first API write "
                      "with schematic open: issue #24966; workaround: move "
                      "a component + Ctrl+S in pcbnew before running). Original error: {e}")
                    .format(op=op_name, e=e)
                )
                raise
        raise last_exc

    def update_items(self, items):
        logger.debug(_("Updating {count} items").format(count=len(items)))
        return self._mutating_call("update_items",
                                   lambda: self._board.update_items(items))

    def create_items(self, items):
        logger.debug(_("Creating {count} items").format(count=len(items)))
        created = self._mutating_call("create_items",
                                      lambda: self._board.create_items(items))
        logger.debug(_("Created {count} items").format(count=len(created)))
        return created

    # --- Specialised actions ---
    def flip_selected(self, footprints: List[FootprintInstance]):
        logger.info(_("Flipping {count} footprints via GUI action").format(count=len(footprints)))
        self._board.clear_selection()
        self._board.add_to_selection(footprints)
        self._kicad.run_action("pcbnew.InteractiveEdit.flip")
        self._board.clear_selection()
        # The flip happens server-side via a GUI action — unlike update_items,
        # it does NOT touch the local FootprintInstance objects' .layer, so
        # the get_footprints() cache (see there) is now stale: any cached fp
        # still reports its PRE-flip layer. flip_manager.flip_if_needed()
        # relies on re-fetching after this call to see the real post-flip
        # layer (otherwise its stale fp objects get pushed straight back via
        # update_items(), silently undoing the flip — found live 2026-07-29:
        # fpga_oscill_r_pi_filter's components landing back on F.Cu).
        self._footprints_cache = None
        logger.debug(_("Flip performed"))

    def commit_with_retry(self, description: str, work_fn, retries: int = 1) -> bool:
        """
        FIXED (2026-07-12): previously `commit = self.begin_commit()` was
        inside try, but if begin_commit() ITSELF crashed (real reproducible
        scenario — see history of stuck IPC session and "KiCad is busy"),
        `commit` remained UNDEFINED, and `except: self.drop_commit(commit)`
        crashed with UnboundLocalError, completely masking the real cause.
        Now commit=None before try, drop_commit is called only if commit was
        actually obtained.
        """
        last_exc = None
        for attempt in range(retries + 1):
            commit = None
            try:
                logger.debug(_("Attempt {attempt}/{total} for {desc}")
                             .format(attempt=attempt+1, total=retries+1, desc=description))
                commit = self.begin_commit()
                work_fn()
                self.push_commit(commit, description)
                return True
            except Exception as e:
                last_exc = e
                if commit is not None:
                    try:
                        self.drop_commit(commit)
                    except Exception as drop_exc:
                        logger.error(_("Failed to roll back transaction {desc}: {e}")
                                     .format(desc=description, e=drop_exc))
                logger.warning(_("Error in transaction {desc} (attempt {attempt}): {type}: {e}")
                               .format(desc=description, attempt=attempt+1,
                                       type=type(e).__name__, e=e))
                if attempt == retries:
                    raise
                time.sleep(0.5)
        if last_exc:
            raise last_exc
        return False

    def create_via(self, position: Vector2, net: Net, drill_mm: float, diameter_mm: float) -> Via:
        logger.debug(_("Creating via at ({x:.3f}, {y:.3f}) mm, net={net}")
                     .format(x=position.x/MM, y=position.y/MM, net=net.name))
        via = Via()
        via.type = ViaType.VT_THROUGH
        via.position = position
        via.net = net
        via.drill_diameter = int(drill_mm * MM)
        via.diameter = int(diameter_mm * MM)
        return via

    def create_track(self, start: Vector2, end: Vector2, width_mm: float,
                     net: Net, layer: BoardLayer) -> Track:
        logger.debug(_("Creating track ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, net={net}")
                     .format(sx=start.x/MM, sy=start.y/MM,
                             ex=end.x/MM, ey=end.y/MM, net=net.name))
        track = Track()
        track.start = start
        track.end = end
        track.width = int(width_mm * MM)
        track.net = net
        track.layer = layer
        return track

    def remove_by_id(self, uuid_str: str) -> bool:
        """
        Deletes an object (via or any other) by its id (UUID string) —
        needed for the placement registry (delete stale via before creating
        a new one at a different location). Returns True if the delete request
        completed without exception — does NOT guarantee that an object with
        that UUID actually existed (stale UUID is not an error, just no‑op).
        """
        from kipy.proto.common.types import base_types_pb2 as common_types_pb2
        kiid = common_types_pb2.KIID()
        kiid.value = uuid_str
        try:
            self._board.remove_items_by_id([kiid])
            return True
        except Exception as e:
            logger.warning(_("Failed to delete object {uuid}: {type}: {e}")
                           .format(uuid=uuid_str, type=type(e).__name__, e=e))
            return False