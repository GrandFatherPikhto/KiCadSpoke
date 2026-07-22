import logging
from typing import List, Tuple, Dict, Optional
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from ...config import Config
from ..commands import MoveCommand, ViaCommand, TrackCommand
from .move_executor import MoveExecutor
from .via_executor import ViaExecutor
from .track_executor import TrackExecutor
from .operation_logger import OperationLogger
from ...registry import PlacementRegistry, TrackRegistry

from ...constants import DEFAULT_BATCH_SIZE

logger = logging.getLogger(__name__)

class BatchExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = DEFAULT_BATCH_SIZE):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size
        self.move_executor = MoveExecutor(adapter, config, batch_size)
        self.via_executor = ViaExecutor(adapter, config, batch_size)
        self.track_executor = TrackExecutor(adapter, config, batch_size)
        self.logger = OperationLogger()
        self._pending_move_log = []
        self._pending_via_log = []

    def execute_moves(self, moves: List[MoveCommand],
                       check_collisions: bool = True,
                       collision_margin_mm: float = 0.2) -> List[str]:
        failed_refs, move_log = self.move_executor.execute_moves(moves, check_collisions, collision_margin_mm)
        self._pending_move_log = move_log
        return failed_refs

    def execute_vias(self, vias: List[ViaCommand], registry: Optional[PlacementRegistry] = None) -> List[str]:
        """
        ИЗМЕНЕНО: больше не пишет лог операции сама (раньше это была
        последняя фаза) — теперь треки идут ПОСЛЕ via, лог откладывается
        до execute_tracks(). Если в конфиге вообще нет треков, cmd_apply
        всё равно вызывает execute_tracks([]) — она допишет лог с пустым
        created_tracks, ничего не потеряется.
        """
        failed_via_owners, via_log = self.via_executor.execute_vias(vias, registry)
        self._pending_via_log = via_log
        return failed_via_owners

    def execute_tracks(self, tracks: List[TrackCommand], registry: Optional[TrackRegistry] = None) -> List[str]:
        failed_track_owners, track_log = self.track_executor.execute_tracks(tracks, registry)
        if self._pending_move_log or self._pending_via_log or track_log:
            self.logger.write_operation_log(self._pending_move_log, self._pending_via_log, track_log)
        self._pending_move_log = []
        self._pending_via_log = []
        return failed_track_owners

    def execute(self, moves: List[MoveCommand], vias: List[ViaCommand],
                tracks: Optional[List[TrackCommand]] = None,
                check_collisions: bool = True,
                collision_margin_mm: float = 0.2) -> Tuple[List[str], List[str], List[str]]:
        failed_refs = self.execute_moves(moves, check_collisions, collision_margin_mm)
        failed_vias = self.execute_vias(vias)
        failed_tracks = self.execute_tracks(tracks or [])
        return failed_refs, failed_vias, failed_tracks