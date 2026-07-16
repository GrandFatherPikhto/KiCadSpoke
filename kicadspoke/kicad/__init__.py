# kicadspoke/kicad/__init__.py
"""
Адаптер для взаимодействия с KiCad через IPC.
Предоставляет унифицированный интерфейс для работы с платой,
компонентами, зонами, цепями и транзакциями.
"""

from .adapter import KiCadBoardAdapter
from .interfaces import IBoardAdapter

__all__ = [
    "KiCadBoardAdapter",
    "IBoardAdapter",
]