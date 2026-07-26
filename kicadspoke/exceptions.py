# kicadspoke/exceptions.py

from kicadspoke.i18n import _

class PlacerError(Exception):
    """Base exception for all placer errors."""
    pass

class BoardNotFoundError(PlacerError):
    """Failed to obtain board from KiCad."""
    pass

class ComponentNotFoundError(PlacerError):
    """Component not found on the board."""
    pass

class GeometryError(PlacerError):
    """Geometry calculation error."""
    pass

class ValidationError(PlacerError):
    """
    Fatal pre‑validation error — detected BEFORE planning/moves,
    program stops without modifying the board.
    """
    pass


def format_fatal_error(title: str, problems: list) -> str:
    """
    Common fatal error formatter – used both in config.py (checks at YAML load)
    and validation.py (checks after connecting to KiCad). Lives here to avoid
    circular imports (validation.py imports config.py).
    """
    lines = [
        "",
        "=" * 70,
        _("  FATAL ERROR: {title}").format(title=title),
        "=" * 70,
    ]
    for p in problems:
        lines.append(f"  ✗ {p}")
    lines.append("=" * 70)
    lines.append(_("Placement stopped, board not modified. Fix the config and run again."))
    lines.append("")
    return "\n".join(lines)