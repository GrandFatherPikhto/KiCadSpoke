import pytest
from kicadspoke.template_extraction import extract_template_from_selection
from kicadspoke.exceptions import ValidationError


@pytest.mark.integration
def test_extract_template_from_selection(adapter):
    """Тестируем извлечение шаблона из текущего выделения."""
    # Предварительно выделите на плате компоненты и via
    # В тесте мы просто проверяем, что функция не падает,
    # если выделение есть, и падает с правильной ошибкой, если нет.
    try:
        template = extract_template_from_selection(adapter, "test_template")
        assert "test_template" in template
        assert "components" in template["test_template"] or "vias" in template["test_template"]
    except ValidationError as e:
        # Если ничего не выделено, это ожидаемо
        assert "нечего извлекать" in str(e)
    except Exception as e:
        pytest.fail(f"Неожиданное исключение: {e}")