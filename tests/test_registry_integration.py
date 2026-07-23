#!/usr/bin/env python3
"""
Интеграционный тест реестра расстановки целиком: ManualPositionCalculator
(генерирует registry_key) -> PlacementRegistry.reconcile() -> исполнение.
Два последовательных "прогона" на одном и том же (моковом) состоянии
реестра между вызовами -- имитация повторного запуска инструмента.

Актуально для новой архитектуры (обобщённые via). TrackRegistry не покрыт,
требует отдельного теста.
"""
import sys
import tempfile
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2, Angle
from kipy.board_types import BoardLayer, Pad, Net

from kicadspoke.config import (
    Config, ThermalViaArrayConfig, ManualSpoke, SpokeTemplate,
    TemplateVia, TemplateComponentSlot, Rule
)
from kicadspoke.placement.services.manual_position_calculator import ManualPositionCalculator
from kicadspoke.registry import PlacementRegistry
from kicadspoke.constants import SPOKE_LEVEL_ROLE_PLACEHOLDER

MM = 1_000_000


def _make_pad(number, x_mm, y_mm, net_name):
    pad = MagicMock(spec=Pad)
    pad.number = number
    pad.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    pad.net.name = net_name
    return pad


def _build_cfg(power_via_offset_across=-1.5):
    template = SpokeTemplate(
        name="t",
        vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=power_via_offset_across,
                          drill_mm=0.3, diameter_mm=0.6)],
    )
    spoke = ManualSpoke(pad="17", template="t", rotation_deg=0.0)
    return Config(
        layer='B.Cu',
        templates={"t": template},
        thermal_via_array=ThermalViaArrayConfig(enabled=False),
        rules=[Rule(net="+3V3", anchor_ref='IC1', spokes=[spoke])],
    )


def test_registry_full_cycle_across_two_runs():
    tmpdir = tempfile.mkdtemp()
    reg_path = os.path.join(tmpdir, "test.registry.json")

    ic1 = MagicMock()
    ic1.definition.items = [_make_pad("17", 50.0, 50.0, "+3V3")]

    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: ic1 if ref == 'IC1' else None
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in fp.definition.items if p.number == num), None
    )
    adapter.get_footprints.return_value = []  # нет компонентов -- только via уровня спицы
    # Изначально нет via на плате
    adapter.get_vias.return_value = []

    # --- Прогон 1: чистый реестр, via должна быть создана ---
    cfg1 = _build_cfg(power_via_offset_across=-1.5)
    calc1 = ManualPositionCalculator(adapter, cfg1)
    _, vias1 = calc1.compute_raw_positions(cfg1.rules)
    assert len(vias1) == 1
    key = vias1[0].registry_key
    expected_key = f"pad:17|t|{SPOKE_LEVEL_ROLE_PLACEHOLDER}|0"
    assert key == expected_key

    reg1 = PlacementRegistry(adapter, reg_path)
    to_create1 = reg1.reconcile(vias1)
    assert len(to_create1) == 1
    reg1.record_created(vias1[0], "uuid-abc")

    # После создания via, добавляем её в живые via адаптера, чтобы следующий reconcile увидел её
    live_via = MagicMock()
    live_via.id.value = "uuid-abc"
    live_via.position = vias1[0].position
    live_via.net.name = vias1[0].net_name
    live_via.drill_diameter = int(vias1[0].drill_mm * MM)
    live_via.diameter = int(vias1[0].diameter_mm * MM)
    adapter.get_vias.return_value = [live_via]

    # --- Прогон 2: тот же конфиг, тот же реестр -- ничего создавать не нужно ---
    calc2 = ManualPositionCalculator(adapter, cfg1)
    _, vias2 = calc2.compute_raw_positions(cfg1.rules)
    reg2 = PlacementRegistry(adapter, reg_path)
    to_create2 = reg2.reconcile(vias2)
    assert len(to_create2) == 0, "конфиг не менялся -- пересоздавать via не нужно"
    adapter.remove_by_id.assert_not_called()

    # --- Прогон 3: пользователь поменял offset_across_mm -- старая via
    # должна быть удалена по uuid, новая помечена к созданию ---
    cfg3 = _build_cfg(power_via_offset_across=-3.0)  # другое значение!
    calc3 = ManualPositionCalculator(adapter, cfg3)
    _, vias3 = calc3.compute_raw_positions(cfg3.rules)
    reg3 = PlacementRegistry(adapter, reg_path)
    to_create3 = reg3.reconcile(vias3)
    assert len(to_create3) == 1
    adapter.remove_by_id.assert_called_once_with("uuid-abc")
    reg3.record_created(vias3[0], "uuid-def")

    # Обновляем живые via: удаляем старую, добавляем новую
    # В реальности adapter.remove_by_id удалила бы via, но мы просто заменяем список
    live_via_new = MagicMock()
    live_via_new.id.value = "uuid-def"
    live_via_new.position = vias3[0].position
    live_via_new.net.name = vias3[0].net_name
    live_via_new.drill_diameter = int(vias3[0].drill_mm * MM)
    live_via_new.diameter = int(vias3[0].diameter_mm * MM)
    adapter.get_vias.return_value = [live_via_new]

    # --- Прогон 4: спицу убрали из конфига вовсе -- prune должен удалить via ---
    adapter.reset_mock()
    adapter.get_vias.return_value = [live_via_new]  # живая via ещё есть
    cfg4 = Config(
        layer='B.Cu', templates={},
        thermal_via_array=ThermalViaArrayConfig(enabled=False), rules=[],
    )
    calc4 = ManualPositionCalculator(adapter, cfg4)
    _, vias4 = calc4.compute_raw_positions(cfg4.rules)
    assert vias4 == []
    reg4 = PlacementRegistry(adapter, reg_path)
    to_create4 = reg4.reconcile(vias4)
    assert to_create4 == []
    # В reconcile будет вызван adapter.remove_by_id для удаления "uuid-def"
    adapter.remove_by_id.assert_called_once_with("uuid-def")