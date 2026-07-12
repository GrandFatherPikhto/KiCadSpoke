# decap_placer/placement/executor.py

import time
import logging
from typing import List, Tuple
from ..kicad.adapter import KiCadBoardAdapter
from ..config import Config
from .planner import MoveCommand, ViaCommand

logger = logging.getLogger(__name__)

class BatchExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = 10):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size
        logger.info(f"Инициализация исполнителя: batch_size={batch_size}")

    def execute(self, moves: List[MoveCommand], vias: List[ViaCommand]) -> Tuple[List[str], List[str]]:
        failed_refs = []
        failed_via_owners = []

        # 1. Флип
        refs_to_flip = [m.ref for m in moves if self._needs_flip(m)]
        if refs_to_flip:
            logger.info(f"Флип {len(refs_to_flip)} компонентов на {self.cfg.side}")
            self._flip_in_batches(refs_to_flip)
            time.sleep(0.5)
        else:
            logger.debug("Флип не требуется")

        # 2. Перемещения
        move_batches = [moves[i:i+self.batch_size] for i in range(0, len(moves), self.batch_size)]
        logger.info(f"Перемещение в {len(move_batches)} батчах")
        for idx, batch in enumerate(move_batches, 1):
            def work():
                items_to_update = []
                for cmd in batch:
                    fp = self.adapter.get_footprint(cmd.ref)
                    if fp is None:
                        logger.warning(f"  {cmd.ref} не найден, пропуск")
                        continue
                    fp.position = cmd.position
                    fp.orientation = cmd.angle
                    items_to_update.append(fp)
                if items_to_update:
                    self.adapter.update_items(items_to_update)
                    logger.debug(f"  обновлено {len(items_to_update)} футпринтов")
            ok = self.adapter.commit_with_retry(f"Move batch {idx}/{len(move_batches)}", work)
            if not ok:
                failed_refs.extend(cmd.ref for cmd in batch)
                logger.error(f"  батч перемещений {idx} провалился")
            else:
                logger.info(f"  батч перемещений {idx} выполнен ({len(batch)} шт.)")

        # 3. Виа
        via_batches = [vias[i:i+self.batch_size] for i in range(0, len(vias), self.batch_size)]
        logger.info(f"Создание виа в {len(via_batches)} батчах")
        for idx, batch in enumerate(via_batches, 1):
            def work():
                new_vias = []
                for cmd in batch:
                    net = self.adapter.get_net_by_name(cmd.net_name)
                    if net is None:
                        logger.warning(f"  цепь {cmd.net_name} не найдена для виа у {cmd.owner_ref}")
                        continue
                    via = self.adapter.create_via(cmd.position, net, cmd.drill_mm, cmd.diameter_mm)
                    new_vias.append(via)
                if new_vias:
                    self.adapter.create_items(new_vias)
                    logger.debug(f"  создано {len(new_vias)} виа")
            ok = self.adapter.commit_with_retry(f"Via batch {idx}/{len(via_batches)}", work)
            if not ok:
                failed_via_owners.extend(cmd.owner_ref for cmd in batch)
                logger.error(f"  батч виа {idx} провалился")
            else:
                logger.info(f"  батч виа {idx} выполнен ({len(batch)} шт.)")

        return failed_refs, failed_via_owners

    def _needs_flip(self, cmd: MoveCommand) -> bool:
        fp = self.adapter.get_footprint(cmd.ref)
        if fp is None:
            return False
        return fp.layer != cmd.layer

    def _flip_in_batches(self, refs: List[str]):
        for i in range(0, len(refs), self.batch_size):
            batch_refs = refs[i:i+self.batch_size]
            fps = [self.adapter.get_footprint(ref) for ref in batch_refs if self.adapter.get_footprint(ref)]
            if fps:
                self.adapter.flip_selected(fps)
                logger.info(f"  флип {len(fps)} шт. (батч {i//self.batch_size + 1})")