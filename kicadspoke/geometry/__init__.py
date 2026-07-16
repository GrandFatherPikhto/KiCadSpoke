# kicadspoke/geometry/__init__.py

from .keepout import Rect, build_keepout, find_free_point, point_is_clear
from .thermal_grid import get_pad_size, compute_thermal_via_grid

__all__ = [
    "Rect",
    "build_keepout",
    "find_free_point",
    "point_is_clear",
    "get_pad_size",
    "compute_thermal_via_grid",
]