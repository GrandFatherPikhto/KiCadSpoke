# kicadstamp/__init__.py
from .i18n import setup_i18n
setup_i18n()

# Single source of truth for the project version — README.md/README_ru.md
# headers and kicadstamp_cli.py's --version both read this, not a separate
# literal. Versioned by session/stage, not by commit: MINOR bumps once per
# notable block of work (e.g. one architecture-refactor session, regardless
# of how many commits it took), PATCH for point fixes made between stages,
# MAJOR reserved for actual breaking changes to the CLI or YAML config
# format (e.g. a renamed field/flag that is not backward compatible).
__version__ = "1.7.0"
