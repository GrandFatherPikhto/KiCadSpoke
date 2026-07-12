# decap_placer/placement/executor.py

import time
import logging
from typing import List, Tuple, Dict

from ..kicad.adapter import KiCadBoardAdapter
from ..config import Config
from ..exceptions import PlacerError
from .planner import MoveCommand, ViaCommand
from .collision import check_collisions as detect_collisions

logger = logging.getLogger(__name__)

class BatchExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = 10):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size
        logger.info(f"Инициализация исполнителя: batch_size={batch_size}")

    def execute(self, moves: List[MoveCommand], vias: List[ViaCommand],
                check_collisions: bool = True,
                collision_margin_mm: float = 0.2) -> Tuple[List[str], List[str]]:
        """
        Применяет команды. Возвращает (failed_refs, failed_via_owners).
        """
        failed_refs = []
        failed_via_owners = []

        # ИСПРАВЛЕНО (2026-07-12): раньше _needs_flip/_flip_in_batches дёргали
        # adapter.get_footprint(ref) (полное линейное сканирование ВСЕХ
        # футпринтов платы) отдельно на каждый ref, причём в списковом
        # включении _flip_in_batches — даже дважды на ref. Теперь строим
        # словарь ref->footprint ОДНИМ вызовом get_footprints() и переиспользуем.
        all_fps = self.adapter.get_footprints()
        fp_by_ref: Dict[str, object] = {fp.reference_field.text.value: fp for fp in all_fps}

        # Проверка коллизий (использует реальные bounding box через adapter)
        if check_collisions and moves:
            ignore_refs = {self.cfg.target_ref}
            conflicts = detect_collisions(moves, all_fps, self.adapter, ignore_refs, collision_margin_mm)
            if conflicts:
                logger.warning(f"Обнаружено {len(conflicts)} потенциальных коллизий:")
                for ref1, ref2, dist in conflicts:
                    logger.warning(f"  {ref1} и {ref2} перекрываются (расст. {dist:.2f} мм)")
            else:
                logger.info("Проверка коллизий: конфликтов не обнаружено")

        # 1. Флип (используем закэшированный словарь — для самого select
        # это безопасно, id объекта не "протухает"; для чтения layer тоже
        # безопасно, т.к. читаем ДО каких-либо изменений)
        refs_to_flip = [m.ref for m in moves if self._needs_flip(m, fp_by_ref)]
        if refs_to_flip:
            logger.info(f"Флип {len(refs_to_flip)} компонентов на {self.cfg.side}")
            self._flip_in_batches(refs_to_flip, fp_by_ref)
            time.sleep(0.5)

            # ВАЖНО: после флипа локальные объекты в fp_by_ref всё ещё
            # хранят СТАРЫЕ layer/orientation (флип — отдельный GUI-action,
            # не update_items). Перечитываем футпринты заново, иначе
            # следующий шаг (простановка position/orientation через
            # update_items) молча откатит флип устаревшими данными.
            all_fps = self.adapter.get_footprints()
            fp_by_ref = {fp.reference_field.text.value: fp for fp in all_fps}
        else:
            logger.debug("Флип не требуется")

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

        # 3. Виа
        via_batches = [vias[i:i+self.batch_size] for i in range(0, len(vias), self.batch_size)]
        logger.info(f"Создание виа в {len(via_batches)} батчах")
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
                    self.adapter.create_items(new_vias)
                    logger.debug(f"  создано {len(new_vias)} виа")
            ok = self.adapter.commit_with_retry(f"Via batch {idx}/{len(via_batches)}", work)
            if not ok:
                failed_via_owners.extend(cmd.owner_ref for cmd in batch)
                logger.error(f"  батч виа {idx} провалился")
            else:
                logger.info(f"  батч виа {idx} выполнен ({len(batch)} шт.)")

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
