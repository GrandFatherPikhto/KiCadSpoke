# decap_placer/exceptions.py

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