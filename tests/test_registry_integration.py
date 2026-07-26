#!/usr/bin/env python3
"""
Интеграционный тест реестра расстановки целиком: ManualPositionCalculator
(генерирует registry_key) -> PlacementRegistry.reconcile() -> исполнение.
Четыре последовательных "прогона" на одном и том же (моковом) состоянии
реестра между вызовами -- имитация повторного запуска инструмента.

ВАЖНО: с тех пор, как reconcile() стал проверять существование via по
ЖИВОЙ плате (adapter.get_vias()), а не слепо доверять JSON — мок должен
отражать реальное состояние "платы" между прогонами: если прогон создал
via с uuid X, следующий прогон должен увидеть эту via через get_vias(),
иначе reconcile() (правильно!) сочтёт её протухшей и пересоздаст. Раньше
этот тест мокал get_vias() пустым списком навсегда и полагался на старое
(уже не существующее) поведение "доверяй JSON" — падал молча в новой
архитектуре, пока не прогнали тесты и не заметили.
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


def _make_live_via(uuid_str, x_mm, y_mm, net_name, drill_mm, diameter_mm):
    """Живая via на "плате" — ровно те поля, что сверяет PlacementRegistry._live_matches."""
    via = MagicMock()
    via.id.value = uuid_str
    via.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    via.net.name = net_name
    via.drill_diameter = int(drill_mm * MM)
    via.diameter = int(diameter_mm * MM)
    return via


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

    # Живые via "на плате" — мутируемый список, отражающий состояние между
    # прогонами (создание/удаление). adapter.get_vias() всегда читает ЕГО.
    live_vias = []

    adapter = MagicMock()
    adapter.get_footprint.side_effect = lambda ref: ic1 if ref == 'IC1' else None
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in fp.definition.items if p.number == num), None
    )
    adapter.get_footprints.return_value = []  # нет компонентов -- только via уровня спицы
    adapter.get_vias.side_effect = lambda: list(live_vias)

    def _remove_by_id(uuid_str):
        live_vias[:] = [v for v in live_vias if v.id.value != uuid_str]
        return True
    adapter.remove_by_id.side_effect = _remove_by_id

    # --- Прогон 1: чистый реестр, via должна быть создана ---
    cfg1 = _build_cfg(power_via_offset_across=-1.5)
    calc1 = ManualPositionCalculator(adapter, cfg1)
    _, vias1, _ = calc1.compute_raw_positions(cfg1.rules)
    assert len(vias1) == 1
    key = vias1[0].registry_key
    expected_key = f"pad:17|t|{SPOKE_LEVEL_ROLE_PLACEHOLDER}|0"
    assert key == expected_key

    reg1 = PlacementRegistry(adapter, reg_path)
    to_create1 = reg1.reconcile(vias1)
    assert len(to_create1) == 1
    reg1.record_created(vias1[0], "uuid-abc")
    # Симулируем реальное создание via на плате -- иначе следующий прогон
    # (по новой, живой-плата-как-источник-истины логике) увидит "нет на
    # плате" и честно пересоздаст, как и должен.
    live_vias.append(_make_live_via("uuid-abc", 50.0, 48.5, "+3V3", 0.3, 0.6))

    # --- Прогон 2: тот же конфиг, тот же реестр, via реально стоит -- ничего создавать не нужно ---
    calc2 = ManualPositionCalculator(adapter, cfg1)
    _, vias2, _ = calc2.compute_raw_positions(cfg1.rules)
    reg2 = PlacementRegistry(adapter, reg_path)
    to_create2 = reg2.reconcile(vias2)
    assert len(to_create2) == 0, "конфиг не менялся, via реально стоит -- пересоздавать не нужно"
    adapter.remove_by_id.assert_not_called()

    # --- Прогон 3: пользователь поменял offset_across_mm -- старая via
    # должна быть удалена по uuid, новая помечена к созданию ---
    cfg3 = _build_cfg(power_via_offset_across=-3.0)  # другое значение!
    calc3 = ManualPositionCalculator(adapter, cfg3)
    _, vias3, _ = calc3.compute_raw_positions(cfg3.rules)
    reg3 = PlacementRegistry(adapter, reg_path)
    to_create3 = reg3.reconcile(vias3)
    assert len(to_create3) == 1
    adapter.remove_by_id.assert_called_once_with("uuid-abc")
    reg3.record_created(vias3[0], "uuid-def")
    live_vias.append(_make_live_via("uuid-def", 50.0, 47.0, "+3V3", 0.3, 0.6))

    # --- Прогон 4: спицу убрали из конфига вовсе -- prune должен удалить via ---
    adapter.reset_mock(side_effect=False)  # reset_mock(side_effect=False) сохраняет
                                           # side_effect у get_vias/remove_by_id,
                                           # сбрасывает только счётчики вызовов
    cfg4 = Config(
        layer='B.Cu', templates={},
        thermal_via_array=ThermalViaArrayConfig(enabled=False), rules=[],
    )
    calc4 = ManualPositionCalculator(adapter, cfg4)
    _, vias4, _ = calc4.compute_raw_positions(cfg4.rules)
    assert vias4 == []
    reg4 = PlacementRegistry(adapter, reg_path)
    to_create4 = reg4.reconcile(vias4)
    assert to_create4 == []
    adapter.remove_by_id.assert_called_once_with("uuid-def")  # protнутая prune'ится
    assert live_vias == [], "prune должен был реально удалить via со \"платы\""
    adapter.remove_by_id.assert_called_once_with("uuid-def")