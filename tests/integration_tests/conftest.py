import pytest
from kipy.geometry import Vector2
from kipy.board_types import BoardLayer

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.config import load_config
from kicadspoke.utils.units import MM
from kicadspoke.placement.commands import ViaCommand
from kicadspoke.registry import PlacementRegistry, registry_path_for_config

TEST_BOARD_PATH = Path("test_boards/10CL006YE144C8G.kicad_pcb")
TEST_CONFIG_PATH = Path("kicadspoke_templates_example.yaml")


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: marks tests that require a running KiCad instance and a PCB board.")

@pytest.fixture(scope="session")
def adapter():
    """Один адаптер на всю сессию тестов."""
    adapter = KiCadBoardAdapter(timeout_ms=30000)
    adapter.refresh_board()
    return adapter


@pytest.fixture(scope="session")
def board(adapter):
    """Доска из адаптера."""
    return adapter._board


@pytest.fixture(scope="session")
def test_config():
    """Загружает тестовый конфиг."""
    return load_config(str(TEST_CONFIG_PATH))


@pytest.fixture(scope="function")
def test_component_ref():
    """Refdes компонента для тестов (должен существовать на плате)."""
    return "C5"


@pytest.fixture(scope="function")
def test_pad_number():
    """Номер пада для тестов."""
    return "17"


@pytest.fixture(scope="function")
def temp_via(adapter):
    """
    Создаёт временную via на GND и удаляет её после теста.
    Возвращает UUID, позицию и цепь.
    """
    net = adapter.get_net_by_name("GND")
    pos = Vector2.from_xy(int(50 * MM), int(50 * MM))
    via = adapter.create_via(pos, net, 0.3, 0.6)

    commit = adapter.begin_commit()
    try:
        created = adapter.create_items([via])
        adapter.push_commit(commit, "test: create temp via")
        via_id = str(created[0].id.value)
    except Exception:
        adapter.drop_commit(commit)
        raise

    yield via_id, pos, net

    # Удаляем via после теста
    adapter.remove_by_id(via_id)
    commit2 = adapter.begin_commit()
    try:
        adapter.push_commit(commit2, "test: remove temp via")
    except Exception:
        adapter.drop_commit(commit2)
        raise


@pytest.fixture(scope="function")
def moved_component(adapter, test_component_ref):
    """
    Перемещает компонент на 1 мм вправо и возвращает обратно после теста.
    Возвращает refdes, исходную позицию и новую позицию.
    """
    fp = adapter.get_footprint(test_component_ref)
    if fp is None:
        pytest.skip(f"Компонент {test_component_ref} не найден на плате")

    original_pos = fp.position
    new_pos = Vector2.from_xy(int(original_pos.x + 1 * MM), int(original_pos.y))

    # Перемещаем
    commit = adapter.begin_commit()
    try:
        fp.position = new_pos
        adapter.update_items([fp])
        adapter.push_commit(commit, "test: move component")
    except Exception:
        adapter.drop_commit(commit)
        raise

    yield test_component_ref, original_pos, new_pos

    # Возвращаем обратно
    fp_after = adapter.get_footprint(test_component_ref)
    if fp_after is None:
        return
    commit2 = adapter.begin_commit()
    try:
        fp_after.position = original_pos
        adapter.update_items([fp_after])
        adapter.push_commit(commit2, "test: restore component")
    except Exception:
        adapter.drop_commit(commit2)
        raise


@pytest.fixture(scope="function")
def flipped_component(adapter, test_component_ref):
    """
    Переворачивает компонент на другую сторону и возвращает обратно после теста.
    Возвращает refdes, исходный слой и целевой слой.
    """
    fp = adapter.get_footprint(test_component_ref)
    if fp is None:
        pytest.skip(f"Компонент {test_component_ref} не найден на плате")

    original_layer = fp.layer
    target_layer = BoardLayer.BL_B_Cu if original_layer == BoardLayer.BL_F_Cu else BoardLayer.BL_F_Cu

    # Флип
    adapter.flip_selected([fp])
    adapter.refresh_board()

    yield test_component_ref, original_layer, target_layer

    # Возвращаем обратно
    fp_after = adapter.get_footprint(test_component_ref)
    if fp_after is None:
        return
    adapter.flip_selected([fp_after])
    adapter.refresh_board()


@pytest.fixture(scope="function")
def registry(adapter, tmp_path):
    """
    Создаёт временный реестр расстановки в tmp_path и возвращает PlacementRegistry.
    После теста реестр не очищается (файл удаляется вместе с tmp_path).
    """
    reg_path = tmp_path / "test.registry.json"
    return PlacementRegistry(adapter, str(reg_path))


@pytest.fixture(scope="function")
def template_extraction(adapter):
    """
    Фикстура для тестов извлечения шаблона.
    Выделяет компоненты и via из конфига и возвращает результат extract_template_from_selection.
    """
    from kicadspoke.template_extraction import extract_template_from_selection
    # Здесь можно предварительно выделить нужные объекты через адаптер,
    # но для простоты мы вызываем функцию без выделения и обрабатываем ошибку.
    # В тестах мы можем использовать эту фикстуру и проверять результат.
    def _extract(name):
        return extract_template_from_selection(adapter, name)
    return _extract