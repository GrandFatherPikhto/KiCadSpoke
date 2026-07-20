import logging
from typing import List, Tuple, Dict
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from ...config import Config
from ..commands import MoveCommand
from ..collision import check_collisions as detect_collisions
from .flip_manager import FlipManager
from .operation_logger import OperationLogger
from .base import layer_to_str

logger = logging.getLogger(__name__)

class MoveExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = 10):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size
        self.flip_manager = FlipManager(adapter, batch_size)

    def execute_moves(self, moves: List[MoveCommand],
                       check_collisions: bool = True,
                       collision_margin_mm: float = 0.2) -> Tuple[List[str], List[Dict]]:
        failed_refs = []
        all_fps = self.adapter.get_footprints()
        fp_by_ref = {fp.reference_field.text.value: fp for fp in all_fps}

        original_states = {}
        for cmd in moves:
            fp = fp_by_ref.get(cmd.ref)
            if fp is not None:
                original_states[cmd.ref] = {
                    'x': fp.position.x,
                    'y': fp.position.y,
                    'angle_deg': fp.orientation.degrees,
                    'layer': layer_to_str(fp.layer)
                }

        if check_collisions and moves:
            ignore_refs = set(self.cfg.anchor_refs)
            conflicts = detect_collisions(moves, all_fps, self.adapter, ignore_refs, collision_margin_mm)
            if conflicts:
                logger.warning(f"Обнаружено {len(conflicts)} потенциальных коллизий:")
                for ref1, ref2, dist in conflicts:
                    logger.warning(f"  {ref1} и {ref2} перекрываются (расст. {dist:.2f} мм)")
            else:
                logger.info("Проверка коллизий: конфликтов не обнаружено")

        fp_by_ref = self.flip_manager.flip_if_needed(moves)

        move_batches = [moves[i:i+self.batch_size] for i in range(0, len(moves), self.batch_size)]
        logger.info(f"Перемещение в {len(move_batches)} батчах")
        for idx, batch in enumerate(move_batches, 1):
            def work(batch=batch, fp_by_ref=fp_by_ref):
                items_to_update = []
                for cmd in batch:
                    fp = fp_by_ref.get(cmd.ref)
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

        move_log = [
            {
                'ref': cmd.ref,
                'original_position': {
                    'x': original_states.get(cmd.ref, {}).get('x', 0),
                    'y': original_states.get(cmd.ref, {}).get('y', 0),
                },
                'original_angle_deg': original_states.get(cmd.ref, {}).get('angle_deg', 0),
                'original_layer': original_states.get(cmd.ref, {}).get('layer', 'F.Cu'),
                'new_position': {'x': cmd.position.x, 'y': cmd.position.y},
                'new_angle_deg': cmd.angle.degrees,
                'layer': layer_to_str(cmd.layer)
            }
            for cmd in moves
        ]

        return failed_refs, move_log