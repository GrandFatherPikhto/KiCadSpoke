import logging
from typing import List, Tuple, Dict, Optional
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from ...config import Config
from ..commands import ViaCommand
from ...registry import PlacementRegistry
from ...utils.units import MM

logger = logging.getLogger(__name__)

class ViaExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = 10):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size

    def execute_vias(self, vias: List[ViaCommand], registry: Optional[PlacementRegistry] = None) -> Tuple[List[str], List[Dict]]:
        failed_via_owners = []
        created_via_log = []

        via_batches = [vias[i:i+self.batch_size] for i in range(0, len(vias), self.batch_size)]
        logger.info(f"Создание виа в {len(via_batches)} батчах")
        for idx, batch in enumerate(via_batches, 1):
            def work(batch=batch):
                new_vias = []
                cmd_for_via = []
                for cmd in batch:
                    net = self.adapter.get_net_by_name(cmd.net_name)
                    if net is None:
                        logger.warning(f"  цепь {cmd.net_name} не найдена для виа у {cmd.owner_ref}")
                        continue
                    via = self.adapter.create_via(cmd.position, net, cmd.drill_mm, cmd.diameter_mm)
                    new_vias.append(via)
                    cmd_for_via.append(cmd)
                if new_vias:
                    created = self.adapter.create_items(new_vias)
                    for cmd, v in zip(cmd_for_via, created):
                        uuid_str = str(v.id.value)
                        created_via_log.append({
                            'uuid': uuid_str,
                            'x_mm': v.position.x / MM,
                            'y_mm': v.position.y / MM,
                            'diameter_mm': v.diameter / MM,
                            'drill_mm': v.drill_diameter / MM,
                            'net_name': v.net.name,
                            'owner_ref': cmd.owner_ref
                        })
                        if registry is not None:
                            registry.record_created(cmd, uuid_str)
                    logger.debug(f"  создано {len(created)} виа")
            ok = self.adapter.commit_with_retry(f"Via batch {idx}/{len(via_batches)}", work)
            if not ok:
                failed_via_owners.extend(cmd.owner_ref for cmd in batch)
                logger.error(f"  батч виа {idx} провалился")
            else:
                logger.info(f"  батч виа {idx} выполнен ({len(batch)} шт.)")

        return failed_via_owners, created_via_log