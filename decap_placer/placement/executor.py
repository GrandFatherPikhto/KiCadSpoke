# decap_placer/placement/executor.py

import time
from typing import List, Tuple
from ..kicad.adapter import KiCadBoardAdapter
from ..config import Config
from .planner import MoveCommand, ViaCommand

class BatchExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = 10):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size

    def execute(self, moves: List[MoveCommand], vias: List[ViaCommand]) -> Tuple[List[str], List[str]]:
        """
        Применяет команды. Возвращает (failed_refs, failed_via_owners).
        """
        failed_refs = []
        failed_via_owners = []

        # 1. Флип нужных компонентов (если слой не совпадает)
        refs_to_flip = [m.ref for m in moves if self._needs_flip(m)]
        if refs_to_flip:
            print(f"Флип {len(refs_to_flip)} компонентов на {self.cfg.side}")
            self._flip_in_batches(refs_to_flip)
            # Даём KiCad время обработать флип
            time.sleep(0.5)

        # 2. Перемещения
        move_batches = [moves[i:i+self.batch_size] for i in range(0, len(moves), self.batch_size)]
        for idx, batch in enumerate(move_batches, 1):
            def work():
                items_to_update = []
                for cmd in batch:
                    fp = self.adapter.get_footprint(cmd.ref)
                    if fp is None:
                        print(f"  [warn] {cmd.ref} не найден, пропуск")
                        continue
                    # Применяем новую позицию и угол
                    fp.position = cmd.position
                    fp.orientation = cmd.angle
                    # Слой уже изменён флипом, не трогаем
                    items_to_update.append(fp)
                if items_to_update:
                    self.adapter.update_items(items_to_update)
            ok = self.adapter.commit_with_retry(f"Move batch {idx}/{len(move_batches)}", work)
            if not ok:
                failed_refs.extend(cmd.ref for cmd in batch)
                print(f"  [error] батч перемещений {idx} провалился")

        # 3. Виа
        via_batches = [vias[i:i+self.batch_size] for i in range(0, len(vias), self.batch_size)]
        for idx, batch in enumerate(via_batches, 1):
            def work():
                new_vias = []
                for cmd in batch:
                    net = self.adapter.get_net_by_name(cmd.net_name)
                    if net is None:
                        print(f"  [warn] цепь {cmd.net_name} не найдена для виа у {cmd.owner_ref}")
                        continue
                    via = self.adapter.create_via(cmd.position, net, cmd.drill_mm, cmd.diameter_mm)
                    new_vias.append(via)
                if new_vias:
                    self.adapter.create_items(new_vias)
            ok = self.adapter.commit_with_retry(f"Via batch {idx}/{len(via_batches)}", work)
            if not ok:
                failed_via_owners.extend(cmd.owner_ref for cmd in batch)
                print(f"  [error] батч виа {idx} провалился")

        return failed_refs, failed_via_owners

    def _needs_flip(self, cmd: MoveCommand) -> bool:
        fp = self.adapter.get_footprint(cmd.ref)
        if fp is None:
            return False
        return fp.layer != cmd.layer

    def _flip_in_batches(self, refs: List[str]):
        """Флип компонентов порциями."""
        for i in range(0, len(refs), self.batch_size):
            batch_refs = refs[i:i+self.batch_size]
            fps = [self.adapter.get_footprint(ref) for ref in batch_refs if self.adapter.get_footprint(ref)]
            if fps:
                self.adapter.flip_selected(fps)
                print(f"  флип {len(fps)} шт. (батч {i//self.batch_size + 1})")