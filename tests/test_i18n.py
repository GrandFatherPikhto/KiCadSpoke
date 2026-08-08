#!/usr/bin/env python3
"""Tests for kicadstamp/i18n.py — language detection precedence and the
setup_i18n() install mechanism. See docs/i18n_translation.md."""
import re
import string
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import gettext
import pytest
from kicadstamp.i18n import detect_language, setup_i18n

ROOT = Path(__file__).parent.parent
RU_PO = ROOT / "locales" / "ru" / "LC_MESSAGES" / "kicadstamp.po"


class TestDetectLanguagePrecedence:
    """LANGUAGE > LC_ALL > LC_MESSAGES > LANG, default English."""

    def test_lang_ru(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "ru_RU.UTF-8")
        assert detect_language() == "ru"

    def test_lang_non_ru(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "de_DE.UTF-8")
        assert detect_language() == "en"

    def test_nothing_set_defaults_to_english(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.delenv("LANG", raising=False)
        assert detect_language() == "en"

    def test_lc_all_overrides_lang(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        assert detect_language() == "ru"

    def test_lc_messages_used_when_lc_all_and_lang_absent(self, monkeypatch):
        """LC_MESSAGES is the POSIX category specifically for message
        language — must be checked before falling back to the catch-all LANG."""
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.setenv("LC_MESSAGES", "ru_RU.UTF-8")
        monkeypatch.delenv("LANG", raising=False)
        assert detect_language() == "ru"

    def test_language_takes_priority_over_everything(self, monkeypatch):
        """LANGUAGE is a gettext extension with the highest precedence."""
        monkeypatch.setenv("LANGUAGE", "ru:en")
        monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        assert detect_language() == "ru"

    def test_language_only_first_entry_matters(self, monkeypatch):
        monkeypatch.setenv("LANGUAGE", "en:ru")
        monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
        assert detect_language() == "en"


class TestSetupI18n:
    """setup_i18n() actually installs a working translation function."""

    def test_returns_detected_language(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "ru_RU.UTF-8")
        assert setup_i18n() == "ru"

    def test_ru_translates_a_known_string(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "ru_RU.UTF-8")
        setup_i18n()
        import kicadstamp.i18n as i18n_module
        assert i18n_module._("Flip performed") == "Флип выполнен"

    def test_en_leaves_a_known_string_in_english(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        setup_i18n()
        import kicadstamp.i18n as i18n_module
        assert i18n_module._("Flip performed") == "Flip performed"

    def test_unknown_locale_falls_back_without_raising(self, monkeypatch):
        """fallback=True in gettext.translation() must swallow a missing
        catalog gracefully — should never crash the whole CLI over this."""
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        lang = setup_i18n()
        assert lang == "en"


def _unescape_po_string(raw: str) -> str:
    """One or more adjacent "..." quoted PO lines -> the decoded string
    (gettext escapes: \\n, \\t, \\", \\\\). Deliberately not str.encode()
    .decode('unicode_escape') — that mangles non-ASCII text, which this
    catalog is full of (Cyrillic)."""
    body = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', raw))
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            n = body[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(n, n))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _po_entries(po_path: Path):
    """Yields (msgid, msgstr) for every entry with BOTH sides non-empty —
    the header entry (msgid "") and untranslated entries (msgstr "") carry
    nothing to check here."""
    text = po_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'msgid ((?:"(?:[^"\\]|\\.)*"\n?)+)msgstr ((?:"(?:[^"\\]|\\.)*"\n?)+)')
    for m in pattern.finditer(text):
        mid = _unescape_po_string(m.group(1))
        mstr = _unescape_po_string(m.group(2))
        if mid and mstr:
            yield mid, mstr


class TestLocaleCatalogIntegrity:
    """Catches malformed RU translations that `pybabel compile` does NOT
    reject — pybabel only validates %-style (python-format), never the
    {name}-style (python-brace-format) placeholders this project actually
    uses everywhere (see i18n.py/adapter.py's f-string-like `_(...).format(
    ...)` pattern).

    Found live 2026-08-08: a translation pass truncated one msgstr mid
    format-spec ('...повтор через {wait:.1f' — no closing brace, no
    trailing text). `pybabel compile` reported success; the break only
    surfaced at runtime, inside a retry-on-busy-KiCad handler
    (kicadstamp/kicad/adapter.py), the first time a RU-locale user hit a
    busy/modal KiCad dialog during a commit — turning a should-be-transient
    retry into a hard ValueError crash. These tests parse the actual .po
    text (not the compiled .mo, which by construction can't contain this
    class of bug post-fix) so a future truncated/malformed translation
    fails the test suite instead of waiting for a user to hit it live."""

    def test_ru_catalog_has_entries(self):
        """Guards the two tests below against a path typo silently
        iterating zero entries and passing vacuously."""
        assert sum(1 for _ in _po_entries(RU_PO)) > 500

    def test_ru_msgstr_is_valid_format_string(self):
        """Every translated msgstr must parse as a syntactically valid
        str.format() template on its own — this is what a truncated msgstr
        (unmatched '{') violates, independent of which placeholder names it
        happens to contain."""
        broken = []
        for mid, mstr in _po_entries(RU_PO):
            try:
                list(string.Formatter().parse(mstr))
            except ValueError as e:
                broken.append((mid[:70], mstr[:70], str(e)))
        assert not broken, (
            "malformed format string(s) in locales/ru/LC_MESSAGES/kicadstamp.po "
            f"(msgid, msgstr, error): {broken}"
        )

    def test_ru_msgstr_placeholders_are_a_subset_of_msgid(self):
        """A msgstr may legitimately DROP a placeholder the English original
        has (e.g. an English-only pluralization suffix that Russian grammar
        doesn't need) — that's safe, .format(**kwargs) ignores unused
        kwargs. But a msgstr must never INVENT a placeholder name absent
        from msgid: the call site's .format(...) only ever supplies the
        English original's names, so a stray {typo} in the translation
        raises KeyError at runtime instead of a silent drop."""
        placeholder_re = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)")
        invented = []
        for mid, mstr in _po_entries(RU_PO):
            mid_names = set(placeholder_re.findall(mid))
            mstr_names = set(placeholder_re.findall(mstr))
            extra = mstr_names - mid_names
            if extra:
                invented.append((mid[:70], mstr[:70], extra))
        assert not invented, (
            "msgstr references placeholder(s) not in msgid (would KeyError "
            f"at runtime) — (msgid, msgstr, extra names): {invented}"
        )
