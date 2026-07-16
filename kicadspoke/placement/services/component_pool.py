# kicadspoke/placement/services/component_pool.py
"""
component_pool.py — подбор конкретных компонентов на роли шаблона по
(реальной цепи, пользовательскому полю Role), а не по явному ref в конфиге.

Пул строится один раз на цепь (rule.net) и разделяется между ВСЕМИ
спицами этого правила — компоненты разбираются по очереди, в
детерминированном (естественном численном: C5 < C10, не как строки)
порядке. Если для какой-то роли не хватило компонентов — фатальная
ошибка (ValidationError), не тихая недостача.
"""
import re
import logging
from typing import Dict, List
from ...kicad.adapter import KiCadBoardAdapter
from ...exceptions import ValidationError

logger = logging.getLogger(__name__)

ROLE_FIELD_NAME = "Role"


def _natural_sort_key(ref: str):
    """C5 < C10 — не как при обычной строковой сортировке ('C10' < 'C5')."""
    parts = re.split(r'(\d+)', ref)
    return [int(p) if p.isdigit() else p for p in parts]


class ComponentPool:
    """
    Пул компонентов для одной цепи (rule.net), сгруппированных по роли.
    Строится один раз, спицы этой цепи разбирают его по очереди через pop().
    """

    def __init__(self, adapter: KiCadBoardAdapter, net_name: str, roles: List[str]):
        self.adapter = adapter
        self.net_name = net_name
        self._pools: Dict[str, List[str]] = {role: [] for role in roles}
        self._build()

    def _build(self):
        for fp in self.adapter.get_footprints():
            role = self.adapter.get_field_value(fp, ROLE_FIELD_NAME)
            if role is None or role not in self._pools:
                continue
            pads = self.adapter.get_footprint_pads(fp)
            nets_on_fp = {p.net.name for p in pads if p.net and p.net.name}
            if self.net_name not in nets_on_fp:
                continue
            ref = fp.reference_field.text.value
            self._pools[role].append(ref)

        for role in self._pools:
            self._pools[role].sort(key=_natural_sort_key)
            logger.debug(f"Пул {self.net_name!r}/{role!r}: {self._pools[role]}")

    def pop(self, role: str, spoke_pad: str) -> str:
        """
        Забирает следующий (по естественному порядку) компонент с ролью
        role. Фатальная ошибка, если пул для этой роли уже исчерпан.
        """
        candidates = self._pools.get(role)
        if candidates is None:
            raise ValidationError(
                f"\nШаблон спицы (пад {spoke_pad}) требует роль {role!r}, "
                f"но пул для цепи {self.net_name!r} про такую роль вообще не знает "
                f"(проверьте список ролей, переданных при построении пула)."
            )
        if not candidates:
            raise ValidationError(
                f"\nНе хватает компонентов с ролью {role!r} на цепи {self.net_name!r} "
                f"для спицы на паде {spoke_pad} — пул исчерпан. "
                f"Проверьте поле {ROLE_FIELD_NAME!r} в схеме: возможно, забыли "
                f"пометить ещё один компонент, или он физически не на этой цепи."
            )
        return candidates.pop(0)

    def remaining_count(self, role: str) -> int:
        return len(self._pools.get(role, []))
