# tests/conftest.py
"""
Forces English gettext output for the whole test suite, regardless of the
calling shell's locale — kicadstamp/__init__.py calls setup_i18n() exactly
once, at first import of the kicadstamp package, reading these same env
vars (see kicadstamp/i18n.py's detect_language() precedence: LANGUAGE >
LC_ALL > LC_MESSAGES > LANG). Most modules bind `_` at import time (`from
kicadstamp.i18n import _`), so whichever language wins at that ONE import
is what every test importing kicadstamp afterwards is stuck with — on a
machine/shell with LANG=ru_RU.UTF-8 (common on this project's dev
machines), that meant tests asserting a hardcoded English substring
against format_fatal_error()'s output (or anything built from it, e.g. the
dry-run report) failed even though nothing was actually broken.

Set at module level, not inside a fixture, so it runs during conftest
collection — before any test module (and therefore before `import
kicadstamp` anywhere) is imported. Same pattern tests/gui/conftest.py
already uses for QT_QPA_PLATFORM=offscreen.

tests/test_i18n.py is unaffected: it monkeypatches these exact vars and
calls setup_i18n() again explicitly inside each test to exercise ru/en/
other locales on demand — this module-level default only decides what the
FIRST, implicit import sees.
"""
import os

os.environ.pop("LANGUAGE", None)
os.environ["LC_ALL"] = "en_US.UTF-8"
os.environ["LANG"] = "en_US.UTF-8"
