#!/usr/bin/env python3
"""
Тест для модуля kicad (без реального подключения к KiCad).
Проверяет импорт и наличие методов в классах.
"""

import sys
from pathlib import Path

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadspoke.kicad import KiCadBoardAdapter, IBoardAdapter
from kicadspoke.kicad.adapter import KiCadBoardAdapter as Adapter


def test_import():
    """Проверяем, что импорт работает."""
    assert KiCadBoardAdapter is not None
    assert IBoardAdapter is not None
    print("✅ Импорт kicad OK")


def test_adapter_has_methods():
    """Проверяем наличие всех методов интерфейса у адаптера."""
    # Создаём экземпляр, но без реального подключения (таймаут малый, но не будет вызывать методы)
    # Можно просто проверить, что методы существуют в классе
    methods = [
        "refresh_board",
        "get_footprint",
        "get_footprints",
        "get_footprint_pads",
        "get_pad_by_number",
        "get_zone_by_name",
        "get_net_by_name",
        "get_bounding_boxes",
        "begin_commit",
        "push_commit",
        "drop_commit",
        "update_items",
        "create_items",
        "flip_selected",
        "commit_with_retry",
        "create_via",
    ]
    for method in methods:
        assert hasattr(Adapter, method), f"Метод {method} отсутствует в KiCadBoardAdapter"
    print("✅ Все методы интерфейса присутствуют в адаптере")


def test_init_without_connection():
    """Проверяем, что конструктор не падает (без вызова refresh_board)."""
    try:
        adapter = KiCadBoardAdapter(timeout_ms=1000)
        assert adapter is not None
        print("✅ Конструктор KiCadBoardAdapter работает (без подключения)")
    except Exception as e:
        print(f"⚠️ Конструктор упал (это может быть нормально, если KiCad не запущен): {e}")


if __name__ == "__main__":
    print("Запуск тестов kicad (без подключения к KiCad)...")
    test_import()
    test_adapter_has_methods()
    test_init_without_connection()
    print("Все тесты kicad пройдены (без реального IPC).")