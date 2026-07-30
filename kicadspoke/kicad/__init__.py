# kicadspoke/kicad/__init__.py
"""
Adapter for communicating with KiCad through IPC.
Provides a unified interface for working with the board,
components, zones, nets and transactions.
"""

from .adapter import KiCadBoardAdapter
from .interfaces import IBoardAdapter

__all__ = [
    "KiCadBoardAdapter",
    "IBoardAdapter",
]