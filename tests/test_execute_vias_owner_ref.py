#!/usr/bin/env python3
"""
Регрессия на находку (2026-07-15): execute_vias() писал в JSON-лог
ПРИБЛИЗИТЕЛЬНЫЙ owner_ref (всегда от первого элемента батча) для КАЖДОЙ
созданной via в батче — независимо от того, какой реальной команде она
соответствовала. Плюс — проверка, что PlacementRegistry.record_created()
реально вызывается для каждой созданной via с правильным uuid.

Актуализировано под новую архитектуру (2026-07-23): лог теперь пишется
в execute_tracks, поэтому тест вызывает execute_tracks([]) после via.
"""
import sys
import json
import tempfile
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2

from kicadspoke.config import Config, ThermalViaArrayConfig
from kicadspoke.placement.executor import BatchExecutor
from kicadspoke.placement.commands import ViaCommand

MM = 1_000_000


def _make_via(x_mm, net_name, owner_ref, registry_key=None):
    return ViaCommand(
        position=Vector2.from_xy(int(x_mm * MM), 0),
        drill_mm=0.3, diameter_mm=0.6, net_name=net_name, owner_ref=owner_ref,
        registry_key=registry_key,
    )


def test_owner_ref_matches_actual_command_not_first_in_batch():
    """Батч из 2 via с РАЗНЫМИ owner_ref -- в логе каждая должна получить
    СВОЙ owner_ref, не owner_ref первой из батча."""
    net = MagicMock()
    net.name = "GND"

    created_via_1 = MagicMock()
    created_via_1.id.value = "uuid-1"
    created_via_1.position = Vector2.from_xy(int(1 * MM), 0)
    created_via_1.diameter = int(0.6 * MM)
    created_via_1.drill_diameter = int(0.3 * MM)
    created_via_1.net.name = "GND"

    created_via_2 = MagicMock()
    created_via_2.id.value = "uuid-2"
    created_via_2.position = Vector2.from_xy(int(2 * MM), 0)
    created_via_2.diameter = int(0.6 * MM)
    created_via_2.drill_diameter = int(0.3 * MM)
    created_via_2.net.name = "GND"

    adapter = MagicMock()
    adapter.get_net_by_name.return_value = net
    adapter.create_items.return_value = [created_via_1, created_via_2]
    adapter.commit_with_retry.side_effect = lambda desc, work: (work(), True)[1]

    # Минимальный конфиг (executor'ам нужен только для передачи в дочерние executors)
    cfg = Config(
        layer='F.Cu',
        templates={},
        thermal_via_array=ThermalViaArrayConfig(enabled=False),
        rules=[],
    )

    old_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    try:
        os.chdir(tmpdir)
        executor = BatchExecutor(adapter, cfg, batch_size=10)

        via_a = _make_via(1, "GND", owner_ref="C5")
        via_b = _make_via(2, "GND", owner_ref="C30")  # ДРУГОЙ owner_ref, не C5!

        # Выполняем via и затем вызываем execute_tracks([]) чтобы записать лог
        executor.execute_vias([via_a, via_b])
        executor.execute_tracks([])  # <-- запись лога

        log_files = list(Path("logs").glob("*.json"))
        assert len(log_files) == 1
        data = json.loads(log_files[0].read_text())
        owners = [v["owner_ref"] for v in data["created_vias"]]
        assert owners == ["C5", "C30"], f"FAIL: owner_ref перепутаны, получили {owners}"
    finally:
        os.chdir(old_cwd)


def test_registry_record_created_called_with_correct_uuid_per_via():
    net = MagicMock()
    net.name = "GND"

    created_via_1 = MagicMock()
    created_via_1.id.value = "uuid-1"
    created_via_1.position = Vector2.from_xy(int(1 * MM), 0)
    created_via_1.diameter = int(0.6 * MM)
    created_via_1.drill_diameter = int(0.3 * MM)
    created_via_1.net.name = "GND"

    created_via_2 = MagicMock()
    created_via_2.id.value = "uuid-2"
    created_via_2.position = Vector2.from_xy(int(2 * MM), 0)
    created_via_2.diameter = int(0.6 * MM)
    created_via_2.drill_diameter = int(0.3 * MM)
    created_via_2.net.name = "GND"

    adapter = MagicMock()
    adapter.get_net_by_name.return_value = net
    adapter.create_items.return_value = [created_via_1, created_via_2]
    adapter.commit_with_retry.side_effect = lambda desc, work: (work(), True)[1]

    cfg = Config(
        layer='F.Cu',
        templates={},
        thermal_via_array=ThermalViaArrayConfig(enabled=False),
        rules=[],
    )

    via_a = _make_via(1, "GND", owner_ref="C5", registry_key="pad:17|t|HEAVY|0")
    via_b = _make_via(2, "GND", owner_ref="C30", registry_key="pad:17|t|LIGHT|0")

    registry = MagicMock()

    old_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    try:
        os.chdir(tmpdir)
        executor = BatchExecutor(adapter, cfg, batch_size=10)
        executor.execute_vias([via_a, via_b], registry=registry)
        # Лог для этого теста не нужен, но если бы вызывали execute_tracks, он бы записался.
        # Проверяем только вызовы record_created
        assert registry.record_created.call_count == 2
        registry.record_created.assert_any_call(via_a, "uuid-1")
        registry.record_created.assert_any_call(via_b, "uuid-2")
    finally:
        os.chdir(old_cwd)