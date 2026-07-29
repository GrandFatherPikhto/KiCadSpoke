# kicadspoke/registry.py
"""
registry.py — placement registry for vias and tracks between runs.

Allows: (1) not touching a via/track that is already exactly in the right place;
(2) if the position in the config changed — delete the old via/track by its
stored UUID and create a new one at the new location; (3) prune — delete
vias/tracks whose keys no longer appear in the current config (spoke/component
removed from YAML entirely).

Composite key — anchor_id/template_name/role/index:
  anchor_id: f"pad:{spoke_pad}" for KiCadSpoke (anchor = IC pad number).
             Future extension: f"ref:{anchor_ref}" for section cloning.
  role: component role (unique within template, see config.py) for component‑level
        vias/tracks, or None for spoke‑level vias/tracks.
  index: 0‑based index within the specific list (vias or tracks) — since roles
         within a template are unique and the order of lists is stable between
         runs, this is sufficient without additional discrimination.

CHANGED (after reports of "glitches mercilessly"): registry.json is now ONLY
an index key->uuid, not the source of truth for position/net/parameters.
Previously "already correctly placed" was decided by comparing numbers from the
JSON with numbers from the same JSON — if a via was manually deleted, undone,
PCB reloaded from git, or a run crashed between record_created() (JSON already
written) and the actual board commit (see known IPC crashes on begin_commit/
push_commit) — the JSON lied that everything was in place, while the board was
empty. Silently, until visual inspection. Now reconcile() reads adapter.get_vias()
once and uses the live via with the stored UUID as the source of truth for
position/net/drill/diameter; a registry entry whose UUID is not found alive on
the board is considered stale (not fatal — just recreate as if the entry never
existed).
"""
import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

from kipy.board_types import BoardLayer
from .placement.commands import ViaCommand, TrackCommand
from .utils.units import MM
from .constants import POSITION_TOLERANCE_MM, SPOKE_LEVEL_ROLE_PLACEHOLDER
from .i18n import _

logger = logging.getLogger(__name__)

_POSITION_TOLERANCE_MM = POSITION_TOLERANCE_MM
_SPOKE_LEVEL_ROLE_PLACEHOLDER = SPOKE_LEVEL_ROLE_PLACEHOLDER


def _layer_to_str(layer: BoardLayer) -> str:
    """BoardLayer -> 'F.Cu'/'B.Cu' — local copy (don't pull from
    .placement.executor.base to avoid import cycles)."""
    return "B.Cu" if layer == BoardLayer.BL_B_Cu else "F.Cu"


def make_registry_key(anchor_id: str, template_name: str, role: Optional[str], index: int) -> str:
    role_part = role if role is not None else _SPOKE_LEVEL_ROLE_PLACEHOLDER
    return f"{anchor_id}|{template_name}|{role_part}|{index}"


def registry_path_for_config(config_path: str) -> str:
    """<config>.yaml -> <config>.registry.json, next to the config itself."""
    p = Path(config_path)
    return str(p.with_suffix("").with_suffix(".registry.json"))


def track_registry_path_for_config(config_path: str) -> str:
    """<config>.yaml -> <config>.tracks.registry.json — separate file from vias,
    record schema is different (two points+width+layer, not drill/diameter)."""
    p = Path(config_path)
    return str(p.with_suffix("").with_suffix(".tracks.registry.json"))


@dataclass
class RegistryEntry:
    uuid: str
    x_mm: float
    y_mm: float
    net: str
    drill_mm: float
    diameter_mm: float


def load_registry(path: str) -> Dict[str, RegistryEntry]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k: RegistryEntry(**v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(_("Failed to read registry {path}: {type}: {e} — "
                         "treating registry as empty (all vias will be created anew)")
                       .format(path=path, type=type(e).__name__, e=e))
        return {}


def save_registry(path: str, entries: Dict[str, RegistryEntry]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: asdict(v) for k, v in entries.items()}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass
class TrackRegistryEntry:
    uuid: str
    start_x_mm: float
    start_y_mm: float
    end_x_mm: float
    end_y_mm: float
    width_mm: float
    net: str
    layer: str


def load_track_registry(path: str) -> Dict[str, TrackRegistryEntry]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k: TrackRegistryEntry(**v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(_("Failed to read track registry {path}: {type}: {e} — "
                         "treating registry as empty (all tracks will be created anew)")
                       .format(path=path, type=type(e).__name__, e=e))
        return {}


def save_track_registry(path: str, entries: Dict[str, TrackRegistryEntry]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: asdict(v) for k, v in entries.items()}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class PlacementRegistry:
    """
    Lives for one run: reconcile() called once before creating vias,
    record_created() as each specific via is successfully created.
    """

    def __init__(self, adapter, path: str):
        self.adapter = adapter
        self.path = path
        self.entries: Dict[str, RegistryEntry] = load_registry(path)

    def _live_matches(self, live_via, via: ViaCommand) -> bool:
        """Checks PLANNED via against REAL via on the board (not against JSON entry)."""
        x_mm = via.position.x / MM
        y_mm = via.position.y / MM
        live_x_mm = live_via.position.x / MM
        live_y_mm = live_via.position.y / MM
        live_net = live_via.net.name if live_via.net else None
        return (
            abs(live_x_mm - x_mm) <= _POSITION_TOLERANCE_MM
            and abs(live_y_mm - y_mm) <= _POSITION_TOLERANCE_MM
            and live_net == via.net_name
            and abs(live_via.drill_diameter / MM - via.drill_mm) < 1e-6
            and abs(live_via.diameter / MM - via.diameter_mm) < 1e-6
        )

    def reconcile(self, planned_vias: List[ViaCommand],
                 known_anchor_ids: Optional[set] = None) -> List[ViaCommand]:
        """
        Returns the subset of planned_vias that actually need to be created
        (already correctly placed ones are excluded). Deletes stale ones by
        stored UUID: both those whose position/parameters changed, and those
        whose key does not appear in this run at all (prune).

        Source of truth for "is it already placed correctly" is the LIVE via on
        the board (adapter.get_vias(), one query for the whole reconcile), not
        the numbers stored in JSON: a registry entry whose UUID is not among the
        live vias is considered stale and recreated as if it never existed.

        known_anchor_ids — the FULL (before any --only filtering) set of
        anchor_id (see clone_anchor_id() in clone_position_calculator.py —
        physical binding anchor_ref/anchor_pad, NOT clone.name, specifically so
        that renaming a clone_placement does not erase history if the physical
        anchor remains the same). Without it (None) prune behaves as before:
        everything not in this run is stale. With it — an entry whose anchor_id
        (entirely, not by name) is still in known_anchor_ids is skipped (not
        pruned), even if it was not among planned_vias in THIS run: it was just
        filtered out by --only, not removed from YAML. Otherwise --only A in one
        run and --only B in the next would mutually delete each other's vias —
        a real bug caught in practice.

        IMPORTANT (found 2026-07-28): known_anchor_ids protection only applies
        to an anchor_id that was NOT SEEN AT ALL this run (--only/--cluster
        excluded the whole item). If the item WAS processed this run (its
        anchor_id appears among seen_keys) but a SPECIFIC key under it is
        missing from planned_vias — e.g. its template's via/track list shrank
        or got reordered — that key is genuinely stale and must be pruned, not
        protected just because the item as a whole is still "known". Before
        this distinction existed, editing a template's via/track list left the
        orphaned old entries stuck on the board forever (real case: 3 tracks
        from an earlier ldo_adj_subsystem revision, at indices no longer used
        by the current template, never got cleaned up run after run).
        """
        to_create: List[ViaCommand] = []
        seen_keys = set()
        live_by_uuid = {str(v.id.value): v for v in self.adapter.get_vias()}

        for via in planned_vias:
            if via.registry_key is None:
                to_create.append(via)
                continue
            seen_keys.add(via.registry_key)

            existing = self.entries.get(via.registry_key)
            if existing is None:
                to_create.append(via)
                continue

            live_via = live_by_uuid.get(existing.uuid)
            if live_via is None:
                logger.warning(_("  {key}: registry has an entry (uuid {uuid}), "
                                 "but no such via is on the board — registry is out of sync "
                                 "(manually deleted, Undo, PCB reloaded from git, or previous run "
                                 "crashed between registry write and board commit); recreating "
                                 "as if the entry never existed")
                               .format(key=via.registry_key, uuid=existing.uuid))
                del self.entries[via.registry_key]
                to_create.append(via)
                continue

            if self._live_matches(live_via, via):
                logger.debug(_("  {key}: already correctly placed (checked against "
                               "live via {uuid}), skipped")
                             .format(key=via.registry_key, uuid=existing.uuid))
                continue

            logger.info(_("  {key}: position/parameters changed, deleting old via ({uuid}) "
                          "and creating a new one")
                        .format(key=via.registry_key, uuid=existing.uuid))
            self.adapter.remove_by_id(existing.uuid)
            del self.entries[via.registry_key]
            to_create.append(via)

        seen_anchor_ids = {key.split('|', 1)[0] for key in seen_keys}

        stale_keys = set()
        for key in set(self.entries.keys()) - seen_keys:
            anchor_id = key.split('|', 1)[0]
            # anchor_id WAS seen this run -> the item itself was processed,
            # this specific key just isn't part of its current plan anymore
            # (template edited) -> genuinely stale, prune below, known_anchor_ids
            # does not apply here (see IMPORTANT note above).
            if (anchor_id not in seen_anchor_ids
                    and known_anchor_ids is not None
                    and anchor_id.startswith(('anchor:', 'role:', 'name:', 'thermal:', 'pad:'))
                    and anchor_id in known_anchor_ids):
                logger.debug(_("  {key}: not processed in this run (--only filtered "
                               "{anchor_id!r}), but it is still in the config — NOT pruned")
                             .format(key=key, anchor_id=anchor_id))
                continue
            stale_keys.add(key)

        for key in stale_keys:
            entry = self.entries.pop(key)
            logger.info(_("  prune: {key} no longer appears in config, deleting via ({uuid})")
                        .format(key=key, uuid=entry.uuid))
            self.adapter.remove_by_id(entry.uuid)

        save_registry(self.path, self.entries)
        return to_create

    def record_created(self, via_cmd: ViaCommand, created_uuid: str) -> None:
        """Called by the executor immediately after successfully creating a specific via."""
        if via_cmd.registry_key is None:
            return
        self.entries[via_cmd.registry_key] = RegistryEntry(
            uuid=created_uuid,
            x_mm=via_cmd.position.x / MM,
            y_mm=via_cmd.position.y / MM,
            net=via_cmd.net_name,
            drill_mm=via_cmd.drill_mm,
            diameter_mm=via_cmd.diameter_mm,
        )
        save_registry(self.path, self.entries)


class TrackRegistry:
    """
    Track placement registry between runs — same logic as PlacementRegistry
    (see its docstring: live board as source of truth, JSON only index key->uuid,
    prune with known_anchor_ids), but matching is by two points + width + layer
    instead of position+drill+diameter. Separate class and separate file on disk
    (track_registry_path_for_config) rather than extending PlacementRegistry —
    the record schema is too different, combining them in one table/class just
    to save code is not worth it (clearer to read separately).
    """

    def __init__(self, adapter, path: str):
        self.adapter = adapter
        self.path = path
        self.entries: Dict[str, TrackRegistryEntry] = load_track_registry(path)

    def _live_matches(self, live_track, track: TrackCommand) -> bool:
        """Checks PLANNED track against REAL track on the board (not against JSON entry)."""
        start_x_mm, start_y_mm = track.start.x / MM, track.start.y / MM
        end_x_mm, end_y_mm = track.end.x / MM, track.end.y / MM
        live_start_x_mm, live_start_y_mm = live_track.start.x / MM, live_track.start.y / MM
        live_end_x_mm, live_end_y_mm = live_track.end.x / MM, live_track.end.y / MM
        live_net = live_track.net.name if live_track.net else None
        return (
            abs(live_start_x_mm - start_x_mm) <= _POSITION_TOLERANCE_MM
            and abs(live_start_y_mm - start_y_mm) <= _POSITION_TOLERANCE_MM
            and abs(live_end_x_mm - end_x_mm) <= _POSITION_TOLERANCE_MM
            and abs(live_end_y_mm - end_y_mm) <= _POSITION_TOLERANCE_MM
            and live_net == track.net_name
            and abs(live_track.width / MM - track.width_mm) < 1e-6
            and _layer_to_str(live_track.layer) == _layer_to_str(track.layer)
        )

    def reconcile(self, planned_tracks: List[TrackCommand],
                 known_anchor_ids: Optional[set] = None) -> List[TrackCommand]:
        """See PlacementRegistry.reconcile — logic is identical, only match
        fields differ (see _live_matches above)."""
        to_create: List[TrackCommand] = []
        seen_keys = set()
        live_by_uuid = {str(t.id.value): t for t in self.adapter.get_tracks()}

        for track in planned_tracks:
            if track.registry_key is None:
                to_create.append(track)
                continue
            seen_keys.add(track.registry_key)

            existing = self.entries.get(track.registry_key)
            if existing is None:
                to_create.append(track)
                continue

            live_track = live_by_uuid.get(existing.uuid)
            if live_track is None:
                logger.warning(_("  {key}: track registry has an entry (uuid {uuid}), "
                                 "but no such track is on the board — registry is out of sync; "
                                 "recreating as if the entry never existed")
                               .format(key=track.registry_key, uuid=existing.uuid))
                del self.entries[track.registry_key]
                to_create.append(track)
                continue

            if self._live_matches(live_track, track):
                logger.debug(_("  {key}: already correctly placed (checked against "
                               "live track {uuid}), skipped")
                             .format(key=track.registry_key, uuid=existing.uuid))
                continue

            logger.info(_("  {key}: geometry/parameters changed, deleting old track ({uuid}) "
                          "and creating a new one")
                        .format(key=track.registry_key, uuid=existing.uuid))
            self.adapter.remove_by_id(existing.uuid)
            del self.entries[track.registry_key]
            to_create.append(track)

        seen_anchor_ids = {key.split('|', 1)[0] for key in seen_keys}

        stale_keys = set()
        for key in set(self.entries.keys()) - seen_keys:
            anchor_id = key.split('|', 1)[0]
            # See PlacementRegistry.reconcile's IMPORTANT note: known_anchor_ids
            # only protects items not seen AT ALL this run, not stale keys within
            # an item that WAS processed but whose template shrank/reordered.
            if (anchor_id not in seen_anchor_ids
                    and known_anchor_ids is not None
                    and anchor_id.startswith(('anchor:', 'role:', 'name:', 'thermal:', 'pad:'))
                    and anchor_id in known_anchor_ids):
                logger.debug(_("  {key}: not processed in this run (--only filtered "
                               "{anchor_id!r}), but it is still in the config — NOT pruned")
                             .format(key=key, anchor_id=anchor_id))
                continue
            stale_keys.add(key)

        for key in stale_keys:
            entry = self.entries.pop(key)
            logger.info(_("  prune: {key} no longer appears in config, deleting track ({uuid})")
                        .format(key=key, uuid=entry.uuid))
            self.adapter.remove_by_id(entry.uuid)

        save_track_registry(self.path, self.entries)
        return to_create

    def record_created(self, track_cmd: TrackCommand, created_uuid: str) -> None:
        """Called by TrackExecutor immediately after successfully creating a specific track."""
        if track_cmd.registry_key is None:
            return
        self.entries[track_cmd.registry_key] = TrackRegistryEntry(
            uuid=created_uuid,
            start_x_mm=track_cmd.start.x / MM,
            start_y_mm=track_cmd.start.y / MM,
            end_x_mm=track_cmd.end.x / MM,
            end_y_mm=track_cmd.end.y / MM,
            width_mm=track_cmd.width_mm,
            net=track_cmd.net_name,
            layer=_layer_to_str(track_cmd.layer),
        )
        save_track_registry(self.path, self.entries)