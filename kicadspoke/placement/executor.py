# kicadspoke/placement/executor.py

import time
import json
import logging
from typing import List, Tuple, Dict, Optional
from datetime import datetime
from pathlib import Path

from ..utils.units import MM
from ..kicad.adapter import KiCadBoardAdapter
from ..config import Config
from ..exceptions import PlacerError
from .commands import MoveCommand, ViaCommand
from .collision import check_collisions as detect_collisions

logger = logging.getLogger(__name__)


def _layer_to_str(layer) -> str:
    """
    ВАЖНО: str(BoardLayer.BL_B_Cu) даёт просто '34' (сырое число enum), а
    НЕ 'B.Cu' — обнаружено при проверке undo (undo.py ищет 'B.Cu' в
    строке, что молча никогда не совпадало бы с сырым числом). Явное
    преобразование вместо str().
    """
    from kipy.board_types import BoardLayer
    return "B.Cu" if layer == BoardLayer.BL_B_Cu else "F.Cu"


class BatchExecutor:
    """
    ИСПРАВЛЕНО (2026-07-15): execute() был единым методом, применявшим
    перемещения и виа одним махом — planner.plan_vias() при этом всегда
    вызывался ДО того, как ход перемещений вообще попадал на плату (весь
    план строился заранее через planner.plan(), одним куском). Для GND
    via, которая теперь берёт РЕАЛЬНЫЙ пад уже перемещённого компонента
    (see via_planner.py), это означало, что via считалась от СТАРОЙ,
    ещё не сдвинутой позиции — тихая, но серьёзная ошибка.

    execute_moves()/execute_vias() разделены явно — вызывающий код
    (kicadspoke.py) обязан сделать adapter.refresh_board() между ними,
    и только тогда planner.plan_vias() вызывать.
    """

    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = 10):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size
        self._pending_move_log: List[Dict] = []
        logger.info(f"Инициализация исполнителя: batch_size={batch_size}")

    def execute_moves(self, moves: List[MoveCommand],
                       check_collisions: bool = True,
                       collision_margin_mm: float = 0.2) -> List[str]:
        """Применяет только перемещения. Возвращает список refdes, которые не удалось применить."""
        failed_refs: List[str] = []

        all_fps = self.adapter.get_footprints()
        fp_by_ref: Dict[str, object] = {fp.reference_field.text.value: fp for fp in all_fps}

        original_states = {}
        for cmd in moves:
            fp = fp_by_ref.get(cmd.ref)
            if fp is not None:
                original_states[cmd.ref] = {
                    'x': fp.position.x,
                    'y': fp.position.y,
                    'angle_deg': fp.orientation.degrees,
                    'layer': _layer_to_str(fp.layer)
                }

        if check_collisions and moves:
            ignore_refs = {self.cfg.target_ref}
            conflicts = detect_collisions(moves, all_fps, self.adapter, ignore_refs, collision_margin_mm)
            if conflicts:
                logger.warning(f"Обнаружено {len(conflicts)} потенциальных коллизий:")
                for ref1, ref2, dist in conflicts:
                    logger.warning(f"  {ref1} и {ref2} перекрываются (расст. {dist:.2f} мм)")
            else:
                logger.info("Проверка коллизий: конфликтов не обнаружено")

        # 1. Флип
        refs_to_flip = [m.ref for m in moves if self._needs_flip(m, fp_by_ref)]
        if refs_to_flip:
            logger.info(f"Флип {len(refs_to_flip)} компонентов на {self.cfg.side}")
            self._flip_in_batches(refs_to_flip, fp_by_ref)
            time.sleep(0.5)
            all_fps = self.adapter.get_footprints()
            fp_by_ref = {fp.reference_field.text.value: fp for fp in all_fps}

        # 2. Перемещения
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

        # Сохраняем для единого JSON-лога, который допишет execute_vias()
        self._pending_move_log = [
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
                'layer': _layer_to_str(cmd.layer)
            }
            for cmd in moves
        ]

        return failed_refs

    def execute_vias(self, vias: List[ViaCommand]) -> List[str]:
        """
        Применяет виа. Пишет ЕДИНЫЙ JSON-лог операции (перемещения из
        предыдущего execute_moves() + эти виа) — undo.py ожидает оба
        раздела в одном файле.
        """
        failed_via_owners: List[str] = []

        via_batches = [vias[i:i+self.batch_size] for i in range(0, len(vias), self.batch_size)]
        logger.info(f"Создание виа в {len(via_batches)} батчах")
        created_via_uuids = []
        for idx, batch in enumerate(via_batches, 1):
            def work(batch=batch):
                new_vias = []
                for cmd in batch:
                    net = self.adapter.get_net_by_name(cmd.net_name)
                    if net is None:
                        logger.warning(f"  цепь {cmd.net_name} не найдена для виа у {cmd.owner_ref}")
                        continue
                    via = self.adapter.create_via(cmd.position, net, cmd.drill_mm, cmd.diameter_mm)
                    new_vias.append(via)
                if new_vias:
                    created = self.adapter.create_items(new_vias)
                    for v in created:
                        created_via_uuids.append({
                            'uuid': str(v.id.value),
                            'x_mm': v.position.x / MM,
                            'y_mm': v.position.y / MM,
                            'diameter_mm': v.diameter / MM,
                            'drill_mm': v.drill_diameter / MM,
                            'net_name': v.net.name,
                            'owner_ref': batch[0].owner_ref  # приблизительно
                        })
                    logger.debug(f"  создано {len(created)} виа")
            ok = self.adapter.commit_with_retry(f"Via batch {idx}/{len(via_batches)}", work)
            if not ok:
                failed_via_owners.extend(cmd.owner_ref for cmd in batch)
                logger.error(f"  батч виа {idx} провалился")
            else:
                logger.info(f"  батч виа {idx} выполнен ({len(batch)} шт.)")

        self._write_operation_log(self._pending_move_log, created_via_uuids)
        self._pending_move_log = []

        return failed_via_owners

    def _write_operation_log(self, move_log: List[Dict], via_log: List[Dict]):
        if not move_log and not via_log:
            return
        try:
            log_data = {
                'timestamp': datetime.now().isoformat(),
                'moves': move_log,
                'created_vias': [
                    {
                        'uuid': v['uuid'], 'x_mm': v['x_mm'], 'y_mm': v['y_mm'],
                        'diameter_mm': v['diameter_mm'], 'drill_mm': v['drill_mm'],
                        'net_name': v['net_name'], 'owner_ref': v['owner_ref']
                    }
                    for v in via_log
                ]
            }
            log_dir = Path("logs")
            log_dir.mkdir(exist_ok=True)
            filename = log_dir / f"operation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
            logger.info(f"Лог операции сохранён в {filename}")
        except Exception as e:
            logger.error(f"Не удалось сохранить лог операции: {e}")

    def execute(self, moves: List[MoveCommand], vias: List[ViaCommand],
                check_collisions: bool = True,
                collision_margin_mm: float = 0.2) -> Tuple[List[str], List[str]]:
        """
        Обратно совместимая обёртка: execute_moves()+execute_vias() без
        перечитывания платы между ними. Годится для тестов/старого кода,
        НЕ для боевого прогона с GND via (см. docstring класса) — там
        нужно adapter.refresh_board() между вызовами по отдельности.
        """
        failed_refs = self.execute_moves(moves, check_collisions, collision_margin_mm)
        failed_via_owners = self.execute_vias(vias)
        return failed_refs, failed_via_owners

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
