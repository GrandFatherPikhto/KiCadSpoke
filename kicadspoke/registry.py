# kicadspoke/registry.py
"""
registry.py — реестр расстановки via между прогонами.

Позволяет: (1) не трогать via, которая уже стоит ровно там, где надо;
(2) если позиция в конфиге изменилась — удалить старую via по
сохранённому uuid и создать новую на месте; (3) prune — удалить via,
чьи ключи больше не встречаются в текущем конфиге (спицу/компонент
убрали из YAML вовсе).

Составной ключ — anchor_id/template_name/role/via_index:
  anchor_id: f"pad:{spoke_pad}" для KiCadSpoke (якорь = номер пада IC).
             Задел на будущее: f"ref:{anchor_ref}" для клонирования секций.
  role: роль компонента (уникальна внутри шаблона, см. config.py) для via
        уровня компонента, или None для via уровня спицы.
  via_index: индекс внутри конкретного списка vias (0-based) — раз роли
        внутри шаблона уникальны, а порядок списка vias стабилен между
        прогонами, этого достаточно, без дополнительного различения.

ИЗМЕНЕНО (после жалобы на «глючит немилосердно»): реестр.json — ТОЛЬКО
индекс key->uuid, не источник истины о позиции/цепи. Раньше "уже стоит
правильно" решалось сверкой чисел из JSON с числами из того же JSON —
если via удалили руками, откатили Undo, PCB перечитали из git, или
прогон упал между record_created() (JSON уже записан) и реальным
коммитом на плату (см. известные креши IPC на begin_commit/push_commit) —
JSON врал, что всё стоит, а на плате было пусто. Тихо, до визуального
осмотра. Теперь reconcile() ОДИН РАЗ читает adapter.get_vias() и live-via
с сохранённым uuid — источник истины по позиции/цепи/сверлу/диаметру;
запись в реестре, чей uuid не найден живьём на плате, считается протухшей
(не фатал — просто пересоздаём, как будто записи не было).
"""
import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

from .placement.commands import ViaCommand
from .utils.units import MM

from .constants import POSITION_TOLERANCE_MM, SPOKE_LEVEL_ROLE_PLACEHOLDER

logger = logging.getLogger(__name__)

_POSITION_TOLERANCE_MM = POSITION_TOLERANCE_MM
_SPOKE_LEVEL_ROLE_PLACEHOLDER = SPOKE_LEVEL_ROLE_PLACEHOLDER

def make_registry_key(anchor_id: str, template_name: str, role: Optional[str], via_index: int) -> str:
    role_part = role if role is not None else _SPOKE_LEVEL_ROLE_PLACEHOLDER
    return f"{anchor_id}|{template_name}|{role_part}|{via_index}"


def registry_path_for_config(config_path: str) -> str:
    """<config>.yaml -> <config>.registry.json, рядом с самим конфигом."""
    p = Path(config_path)
    return str(p.with_suffix("").with_suffix(".registry.json"))


@dataclass
class RegistryEntry:
    uuid: str
    x_mm: float
    y_mm: float
    net: str
    drill_mm: float
    diameter_mm: float


def load_registry(path: str) -> Dict[str, RegistryEntry]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k: RegistryEntry(**v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(f"Не удалось прочитать реестр {path}: {type(e).__name__}: {e} — "
                       f"считаю реестр пустым (все via будут созданы заново)")
        return {}


def save_registry(path: str, entries: Dict[str, RegistryEntry]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: asdict(v) for k, v in entries.items()}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class PlacementRegistry:
    """
    Живёт один прогон: reconcile() один раз перед созданием via,
    record_created() — по мере успешного создания каждой конкретной via.
    """

    def __init__(self, adapter, path: str):
        self.adapter = adapter
        self.path = path
        self.entries: Dict[str, RegistryEntry] = load_registry(path)

    def _live_matches(self, live_via, via: ViaCommand) -> bool:
        """Сверка ПЛАНИРУЕМОЙ via с РЕАЛЬНОЙ via на плате (не с записью в JSON)."""
        x_mm = via.position.x / MM
        y_mm = via.position.y / MM
        live_x_mm = live_via.position.x / MM
        live_y_mm = live_via.position.y / MM
        live_net = live_via.net.name if live_via.net else None
        return (
            abs(live_x_mm - x_mm) <= _POSITION_TOLERANCE_MM
            and abs(live_y_mm - y_mm) <= _POSITION_TOLERANCE_MM
            and live_net == via.net_name
            and abs(live_via.drill_diameter / MM - via.drill_mm) < 1e-6
            and abs(live_via.diameter / MM - via.diameter_mm) < 1e-6
        )

    def reconcile(self, planned_vias: List[ViaCommand],
                 known_clone_names: Optional[set] = None) -> List[ViaCommand]:
        """
        Возвращает подмножество planned_vias, которое РЕАЛЬНО нужно
        создать (уже стоящие правильно — исключены). Удаляет устаревшие
        по сохранённому uuid: и те, что изменили позицию/параметры, и те,
        чей ключ вообще не встретился в этом прогоне (prune).

        Источник истины по "стоит ли уже правильно" — ЖИВАЯ via на плате
        (adapter.get_vias(), один запрос на весь reconcile), а не
        сохранённые в JSON числа: запись реестра, чей uuid не находится
        среди живых via, считается протухшей и пересоздаётся, как будто
        записи не было вовсе.

        known_clone_names — ПОЛНЫЙ (до всякой --clone-placement
        фильтрации) набор имён clone_placements из конфига. Без него
        (None) prune ведёт себя по-старому: всё, чего нет в этом прогоне —
        устарело. С ним — запись с anchor_id вида "name:X" пропускается
        (не prune'ится), если X всё ещё есть в known_clone_names, даже
        если этого X не было среди planned_vias ЭТОГО прогона: значит,
        его просто отфильтровали через --clone-placement, а не убрали
        из YAML. Иначе --clone-placement A на одном прогоне и
        --clone-placement B на следующем взаимно удаляли бы via друг
        друга — реальный баг, пойманный на практике.
        """
        to_create: List[ViaCommand] = []
        seen_keys = set()
        live_by_uuid = {str(v.id.value): v for v in self.adapter.get_vias()}

        for via in planned_vias:
            if via.registry_key is None:
                to_create.append(via)
                continue
            seen_keys.add(via.registry_key)

            existing = self.entries.get(via.registry_key)
            if existing is None:
                to_create.append(via)
                continue

            live_via = live_by_uuid.get(existing.uuid)
            if live_via is None:
                logger.warning(f"  {via.registry_key}: в реестре есть запись (uuid "
                               f"{existing.uuid}), но такой via на плате НЕТ — реестр "
                               f"рассинхронизирован (удалили руками, Undo, PCB "
                               f"перечитан из git, или прошлый прогон упал между "
                               f"записью в реестр и коммитом на плату); пересоздаю "
                               f"как будто записи не было")
                del self.entries[via.registry_key]
                to_create.append(via)
                continue

            if self._live_matches(live_via, via):
                logger.debug(f"  {via.registry_key}: уже стоит правильно (проверено "
                            f"по живой via {existing.uuid}), пропуск")
                continue

            logger.info(f"  {via.registry_key}: позиция/параметры изменились, "
                       f"удаляю старую via ({existing.uuid}) и создаю новую")
            self.adapter.remove_by_id(existing.uuid)
            del self.entries[via.registry_key]
            to_create.append(via)

        stale_keys = set()
        for key in set(self.entries.keys()) - seen_keys:
            anchor_id = key.split('|', 1)[0]
            if (known_clone_names is not None and anchor_id.startswith('name:')
                    and anchor_id[len('name:'):] in known_clone_names):
                logger.debug(f"  {key}: не обработан в этом прогоне (--clone-placement "
                            f"отфильтровал {anchor_id!r}), но он есть в конфиге — "
                            f"НЕ prune'ится")
                continue
            stale_keys.add(key)

        for key in stale_keys:
            entry = self.entries.pop(key)
            logger.info(f"  prune: {key} больше не встречается в конфиге, удаляю via ({entry.uuid})")
            self.adapter.remove_by_id(entry.uuid)

        save_registry(self.path, self.entries)
        return to_create

    def record_created(self, via_cmd: ViaCommand, created_uuid: str) -> None:
        """Вызывается executor'ом сразу после успешного создания конкретной via."""
        if via_cmd.registry_key is None:
            return
        self.entries[via_cmd.registry_key] = RegistryEntry(
            uuid=created_uuid,
            x_mm=via_cmd.position.x / MM,
            y_mm=via_cmd.position.y / MM,
            net=via_cmd.net_name,
            drill_mm=via_cmd.drill_mm,
            diameter_mm=via_cmd.diameter_mm,
        )
        save_registry(self.path, self.entries)