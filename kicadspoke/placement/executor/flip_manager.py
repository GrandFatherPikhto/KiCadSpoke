# kicadspoke/placement/executor/flip_manager.py
import logging
import time
from typing import List, Dict
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from ..commands import MoveCommand
from .base import layer_to_str

logger = logging.getLogger(__name__)

class FlipManager:
    def __init__(self, adapter: KiCadBoardAdapter, batch_size: int = 10):
        self.adapter = adapter
        self.batch_size = batch_size

    def flip_if_needed(self, moves: List[MoveCommand]) -> Dict[str, object]:
        """Возвращает словарь ref->footprint после возможного флипа."""
        all_fps = self.adapter.get_footprints()
        fp_by_ref = {fp.reference_field.text.value: fp for fp in all_fps}

        refs_to_flip = [m.ref for m in moves if self._needs_flip(m, fp_by_ref)]
        if refs_to_flip:
            logger.info(f"Флип {len(refs_to_flip)} компонентов")
            self._flip_in_batches(refs_to_flip, fp_by_ref)
            time.sleep(0.5)
            # Перечитываем футпринты после флипа
            all_fps = self.adapter.get_footprints()
            fp_by_ref = {fp.reference_field.text.value: fp for fp in all_fps}
        return fp_by_ref

    def _needs_flip(self, cmd: MoveCommand, fp_by_ref: Dict[str, object]) -> bool:
        fp = fp_by_ref.get(cmd.ref)
        if fp is None:
            return False
        return fp.layer != cmd.layer

    def _flip_in_batches(self, refs: List[str], fp_by_ref: Dict[str, object]):
        for i in range(0, len(refs), self.batch_size):
            batch_refs = refs[i:i+self.batch_size]
            fps = [fp_by_ref[ref] for ref in batch_refs if ref in fp_by_ref]
            if fps:
                self.adapter.flip_selected(fps)
                logger.info(f"  флип {len(fps)} шт. (батч {i//self.batch_size + 1})")