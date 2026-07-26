#!/usr/bin/env python
"""
Обновление файлов перевода gettext.
Запускать из корня проекта: python tools/update_i18n.py
Требуется установленный Babel (pip install babel).
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent

# Замороженные архивы (files/, old/, ...) — не наш код, сканировать не нужно;
# у pybabel extract нет ключа в babel.cfg для этого, только --ignore-dirs.
# --ignore-dirs ПОЛНОСТЬЮ заменяет дефолт (".* ._"), не дополняет его — поэтому
# дефолтные шаблоны дописаны явно, чтобы не потерять их.
IGNORE_DIRS = [".*", "._", "files", "old", "arch", "test_sample", "venv"]


def run(cmd):
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def main():
    # 1. Извлечение строк в .pot
    # --ignore-dirs принимает ОДНУ строку с шаблонами через пробел, не повторяемый флаг
    run(["pybabel", "extract", "-F", "babel.cfg",
         "--ignore-dirs", " ".join(IGNORE_DIRS),
         "-o", "messages.pot", "."])

    # 2. Обновление/создание .po для каждого языка
    for lang in ("en", "ru"):
        po_file = ROOT / "locales" / lang / "LC_MESSAGES" / "kicadspoke.po"
        if po_file.exists():
            run(["pybabel", "update", "-i", "messages.pot", "-d", "locales", "-l", lang, "-D", "kicadspoke"])
        else:
            run(["pybabel", "init", "-i", "messages.pot", "-d", "locales", "-l", lang, "-D", "kicadspoke"])

    # 3. Компиляция .mo
    for lang in ("en", "ru"):
        run(["pybabel", "compile", "-d", "locales", "-l", lang, "-D", "kicadspoke"])

    # 4. Удаление временного .pot
    (ROOT / "messages.pot").unlink(missing_ok=True)
    print("✅ Переводы обновлены.")


if __name__ == "__main__":
    main()