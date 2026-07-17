# kicadspoke/placement/executor/operation_logger.py
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class OperationLogger:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)

    def write_operation_log(self, move_log: List[Dict], via_log: List[Dict]) -> Optional[Path]:
        if not move_log and not via_log:
            return None
        try:
            self.log_dir.mkdir(exist_ok=True)
            log_data = {
                'timestamp': datetime.now().isoformat(),
                'moves': move_log,
                'created_vias': via_log
            }
            filename = self.log_dir / f"operation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
            logger.info(f"Лог операции сохранён в {filename}")
            return filename
        except Exception as e:
            logger.error(f"Не удалось сохранить лог операции: {e}")
            return None