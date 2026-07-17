import logging
from typing import List, Tuple, Dict, Optional
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from ...config import Config
from ..commands import MoveCommand, ViaCommand
from .move_executor import MoveExecutor
from .via_executor import ViaExecutor
from .operation_logger import OperationLogger
from ...registry import PlacementRegistry

from ...constants import DEFAULT_BATCH_SIZE

logger = logging.getLogger(__name__)

class BatchExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = DEFAULT_BATCH_SIZE):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size
        self.move_executor = MoveExecutor(adapter, config, batch_size)
        self.via_executor = ViaExecutor(adapter, config, batch_size)
        self.logger = OperationLogger()
        self._pending_move_log = []

    def execute_moves(self, moves: List[MoveCommand],
                       check_collisions: bool = True,
                       collision_margin_mm: float = 0.2) -> List[str]:
        failed_refs, move_log = self.move_executor.execute_moves(moves, check_collisions, collision_margin_mm)
        self._pending_move_log = move_log
        return failed_refs

    def execute_vias(self, vias: List[ViaCommand], registry: Optional[PlacementRegistry] = None) -> List[str]:
        failed_via_owners, via_log = self.via_executor.execute_vias(vias, registry)
        if self._pending_move_log or via_log:
            self.logger.write_operation_log(self._pending_move_log, via_log)
        self._pending_move_log = []
        return failed_via_owners

    def execute(self, moves: List[MoveCommand], vias: List[ViaCommand],
                check_collisions: bool = True,
                collision_margin_mm: float = 0.2) -> Tuple[List[str], List[str]]:
        failed_refs = self.execute_moves(moves, check_collisions, collision_margin_mm)
        failed_vias = self.execute_vias(vias)
        return failed_refs, failed_vias