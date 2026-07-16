# kicadspoke/exceptions.py

class PlacerError(Exception):
    """Базовое исключение для всех ошибок планера."""
    pass

class BoardNotFoundError(PlacerError):
    """Не удалось получить плату из KiCad."""
    pass

class ComponentNotFoundError(PlacerError):
    """Компонент не найден на плате."""
    pass

class GeometryError(PlacerError):
    """Ошибка в геометрических расчётах."""
    pass

class ValidationError(PlacerError):
    """
    Фатальная ошибка предварительной проверки конфигурации — обнаружена
    ДО планирования/перемещений, программа должна остановиться, ничего
    не тронув на плате.
    """
    pass


def format_fatal_error(title: str, problems: list) -> str:
    """
    Общий форматтер фатальной ошибки — используется и config.py (проверки
    на этапе чтения YAML, до подключения к KiCad), и validation.py
    (проверки после подключения). Живёт здесь, а не в validation.py, чтобы
    избежать циклического импорта (validation.py импортирует config.py).
    """
    lines = [
        "",
        "=" * 70,
        f"  ФАТАЛЬНАЯ ОШИБКА: {title}",
        "=" * 70,
    ]
    for p in problems:
        lines.append(f"  ✗ {p}")
    lines.append("=" * 70)
    lines.append("Расстановка остановлена, плата не тронута. Исправьте конфиг и запустите заново.")
    lines.append("")
    return "\n".join(lines)