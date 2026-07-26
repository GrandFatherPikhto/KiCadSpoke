# kicadspoke/i18n.py
import os
import sys
import gettext
import locale
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
LOCALE_DIR = ROOT_DIR / "locales"

# Глобальная функция перевода, будет установлена в setup_i18n
_ = gettext.gettext  # fallback

def detect_language() -> str:
    """
    Определяет язык из переменных окружения или системной локали (Windows fallback).
    Приоритет: LC_ALL > LANG. Если ни одна не задана или не начинается с 'ru' → английский.
    На Windows, если переменные не заданы, смотрим системную локаль (GetUserDefaultUILanguage).
    """
    # 1. Переменные окружения (Unix-стиль, приоритет над системным)
    lang_env = os.environ.get("LC_ALL") or os.environ.get("LANG")
    if lang_env:
        return "ru" if lang_env.startswith("ru") else "en"

    # 2. Windows fallback (если переменные не заданы)
    if sys.platform == "win32":
        try:
            import ctypes
            # LCID для русского языка = 1049 (0x419)
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if lcid == 1049:
                return "ru"
        except Exception:
            pass
        try:
            loc = locale.getlocale()[0]  # (language_code, encoding)
            if loc and loc.startswith("ru"):
                return "ru"
        except Exception:
            pass

    # 3. По умолчанию английский
    return "en"


def setup_i18n():
    """
    Инициализирует gettext: выбирает язык, загружает перевод, устанавливает _() глобально.
    Возвращает выбранный код языка.
    """
    global _
    lang = detect_language()
    try:
        translation = gettext.translation(
            "kicadspoke",
            localedir=str(LOCALE_DIR),
            languages=[lang],
            fallback=True,
        )
    except Exception:
        translation = gettext.NullTranslations()
    translation.install()
    _ = translation.gettext  # теперь _ доступна как глобальная функция в модуле
    # print(lang)
    return lang