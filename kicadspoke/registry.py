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

НАМЕРЕННО НЕ реализовано (осознанно отложено, см. обсуждение):
  - Сверка реестра с реальным состоянием платы при ручном вмешательстве
    человека мимо инструмента — реестр авторитетен только для того, что
    сделано ЧЕРЕЗ этот инструмент.
  - Протухший uuid (via удалена в KiCad руками) — просто graceful: не
    падаем, логируем предупреждение, действуем как будто ключа не было.
"""
import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

from .placement.commands import ViaCommand
from .utils.units import MM

logger = logging.getLogger(__name__)

_POSITION_TOLERANCE_MM = 0.01
_SPOKE_LEVEL_ROLE_PLACEHOLDER = "__spoke__"


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

    def _matches(self, entry: RegistryEntry, via: ViaCommand) -> bool:
        x_mm = via.position.x / MM
        y_mm = via.position.y / MM
        return (
            abs(entry.x_mm - x_mm) <= _POSITION_TOLERANCE_MM
            and abs(entry.y_mm - y_mm) <= _POSITION_TOLERANCE_MM
            and entry.net == via.net_name
            and abs(entry.drill_mm - via.drill_mm) < 1e-6
            and abs(entry.diameter_mm - via.diameter_mm) < 1e-6
        )

    def reconcile(self, planned_vias: List[ViaCommand]) -> List[ViaCommand]:
        """
        Возвращает подмножество planned_vias, которое РЕАЛЬНО нужно
        создать (уже стоящие правильно — исключены). Удаляет устаревшие
        по сохранённому uuid: и те, что изменили позицию/параметры, и те,
        чей ключ вообще не встретился в этом прогоне (prune).
        """
        to_create: List[ViaCommand] = []
        seen_keys = set()

        for via in planned_vias:
            if via.registry_key is None:
                to_create.append(via)
                continue
            seen_keys.add(via.registry_key)

            existing = self.entries.get(via.registry_key)
            if existing is None:
                to_create.append(via)
                continue
            if self._matches(existing, via):
                logger.debug(f"  {via.registry_key}: уже стоит правильно, пропуск")
                continue

            logger.info(f"  {via.registry_key}: позиция/параметры изменились, "
                       f"удаляю старую via ({existing.uuid}) и создаю новую")
            self.adapter.remove_by_id(existing.uuid)
            del self.entries[via.registry_key]
            to_create.append(via)

        stale_keys = set(self.entries.keys()) - seen_keys
        for key in stale_keys:
            entry = self.entries.pop(key)
            logger.info(f"  prune: {key} больше не встречается в конфиге, удаляю via ({entry.uuid})")
            self.adapter.remove_by_id(entry.uuid)

        if stale_keys or any(v.registry_key in self.entries for v in to_create):
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
