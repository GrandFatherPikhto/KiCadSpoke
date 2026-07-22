import logging
from typing import List, Tuple, Dict, Optional
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from ...config import Config
from ..commands import TrackCommand
from ...registry import TrackRegistry
from ...utils.units import MM

logger = logging.getLogger(__name__)

class TrackExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = 10):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size

    def execute_tracks(self, tracks: List[TrackCommand],
                       registry: Optional[TrackRegistry] = None) -> Tuple[List[str], List[Dict]]:
        failed_track_owners = []
        created_track_log = []

        track_batches = [tracks[i:i+self.batch_size] for i in range(0, len(tracks), self.batch_size)]
        logger.info(f"Создание треков в {len(track_batches)} батчах")
        for idx, batch in enumerate(track_batches, 1):
            def work(batch=batch):
                new_tracks = []
                cmd_for_track = []
                for cmd in batch:
                    net = self.adapter.get_net_by_name(cmd.net_name)
                    if net is None:
                        logger.warning(f"  цепь {cmd.net_name} не найдена для трека у {cmd.owner_ref}")
                        continue
                    track = self.adapter.create_track(cmd.start, cmd.end, cmd.width_mm, net, cmd.layer)
                    new_tracks.append(track)
                    cmd_for_track.append(cmd)
                if new_tracks:
                    created = self.adapter.create_items(new_tracks)
                    for cmd, t in zip(cmd_for_track, created):
                        uuid_str = str(t.id.value)
                        created_track_log.append({
                            'uuid': uuid_str,
                            'start_x_mm': t.start.x / MM,
                            'start_y_mm': t.start.y / MM,
                            'end_x_mm': t.end.x / MM,
                            'end_y_mm': t.end.y / MM,
                            'width_mm': t.width / MM,
                            'net_name': t.net.name,
                            'owner_ref': cmd.owner_ref
                        })
                        if registry is not None:
                            registry.record_created(cmd, uuid_str)
                    logger.debug(f"  создано {len(created)} треков")
            ok = self.adapter.commit_with_retry(f"Track batch {idx}/{len(track_batches)}", work)
            if not ok:
                failed_track_owners.extend(cmd.owner_ref for cmd in batch)
                logger.error(f"  батч треков {idx} провалился")
            else:
                logger.info(f"  батч треков {idx} выполнен ({len(batch)} шт.)")

        return failed_track_owners, created_track_log