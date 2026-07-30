#!/usr/bin/env python3
"""Tests for kicadstamp/i18n.py — language detection precedence and the
setup_i18n() install mechanism. See docs/i18n_translation.md."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import gettext
import pytest
from kicadstamp.i18n import detect_language, setup_i18n


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
