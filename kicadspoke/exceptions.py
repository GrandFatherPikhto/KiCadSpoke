# kicadspoke/exceptions.py

import difflib
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


def check_unknown_keys(data: dict, known_keys: set, title: str, extra_hint: str = "") -> None:
    """
    Fatal if data has keys outside known_keys. Every YAML block in this
    project (clone_placements, rules, extract_profiles, clone_profiles) is
    read field-by-field via dict.get() — a typo'd or wrong-separator key
    (e.g. 'origin-by-via-net' instead of 'origin_by_via_net') is otherwise
    silently ignored, no error at all: a real class of bug hit live on
    boards/3ch-awg-tia (dash in anchor_sheet/origin_by_via_net). Suggests the
    closest known key via difflib, same UX as _CLONE_PLACEMENT_KNOWN_KEYS's
    original check (config/loader.py) this generalises from.

    extra_hint — optional extra parenthetical appended after "quiet bugs"
    and before the colon, e.g. " (e.g. 'pad' won't work; use 'anchor_pad')".
    """
    unknown = set(data.keys()) - known_keys
    if not unknown:
        return
    problems = []
    for key in sorted(unknown):
        close = difflib.get_close_matches(key, known_keys, n=1)
        if not close:
            close = [k for k in sorted(known_keys) if key in k or k in key]
        hint = _(" — did you mean {suggestion!r}?").format(suggestion=close[0]) if close else ""
        problems.append(f"{key!r}{hint}")
    raise ValidationError(format_fatal_error(
        title,
        [_("unrecognised keys are silently ignored – common source of quiet bugs{extra}: {problems}")
         .format(extra=extra_hint, problems=', '.join(problems))]
    ))