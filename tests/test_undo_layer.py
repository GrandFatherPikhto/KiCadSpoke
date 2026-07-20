#!/usr/bin/env python3
"""
Регрессия на находку (2026-07-15): executor.py никогда не записывал
original_layer в JSON-лог операции, из-за чего undo.py всегда откатывался
на захардкоженный дефолт 'F.Cu' — совпадало по чистой случайности при
первом прогоне (компонент действительно на F.Cu), но ломалось при втором
прогоне без undo между ними (компонент уже на B.Cu — undo ошибочно
флипал его обратно на F.Cu).

Заодно: str(BoardLayer.BL_B_Cu) даёт сырое число ('34'), а НЕ 'B.Cu' —
если бы слой просто взяли через str(), проверка 'B.Cu' in ... в undo.py
всё равно никогда бы не сработала. Нужно явное преобразование.
"""
import sys
import json
import tempfile
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock
from kipy.geometry import Vector2, Angle
from kipy.board_types import BoardLayer

# via_planner.py теперь на новой модели (шаблоны), заглушка под старые
# классы больше не нужна.

from kicadspoke.placement.executor.base import layer_to_str as _layer_to_str
from kicadspoke.placement.executor import BatchExecutor
from kicadspoke.placement.commands import MoveCommand

MM = 1_000_000


class TestLayerToStr:
    def test_gives_real_strings_not_raw_enum_numbers(self):
        """str(BoardLayer.BL_B_Cu) даёт '34', а не 'B.Cu' — проверяем,
        что _layer_to_str даёт то, что реально ожидает undo.py."""
        assert _layer_to_str(BoardLayer.BL_F_Cu) == "F.Cu"
        assert _layer_to_str(BoardLayer.BL_B_Cu) == "B.Cu"


class TestOriginalLayerCapture:
    def test_original_layer_written_correctly_when_already_on_back(self):
        """Компонент УЖЕ на B.Cu до этого прогона (второй прогон без undo
        между ними) — original_layer в логе должен быть 'B.Cu', а не
        ошибочный дефолт 'F.Cu'."""
        fp = MagicMock()
        fp.reference_field.text.value = "C39"
        fp.position = Vector2.from_xy(int(50 * MM), int(50 * MM))
        fp.orientation = Angle.from_degrees(90.0)
        fp.layer = BoardLayer.BL_B_Cu

        adapter = MagicMock()
        adapter.get_footprints.return_value = [fp]
        adapter._board = MagicMock()
        adapter.commit_with_retry.return_value = True

        cfg = MagicMock()
        cfg.target_ref = "IC1"
        cfg.layer='B.Cu'
        executor = BatchExecutor(adapter, cfg, batch_size=10)

        move = MoveCommand(ref="C39", position=Vector2.from_xy(int(51 * MM), int(51 * MM)),
                          angle=Angle.from_degrees(180.0), layer=BoardLayer.BL_B_Cu)

        old_cwd = os.getcwd()
        tmpdir = tempfile.mkdtemp()
        try:
            os.chdir(tmpdir)
            executor.execute([move], [], check_collisions=False)
            log_files = list(Path("logs").glob("*.json"))
            assert len(log_files) == 1
            data = json.loads(log_files[0].read_text())
            assert data["moves"][0]["original_layer"] == "B.Cu"
        finally:
            os.chdir(old_cwd)
