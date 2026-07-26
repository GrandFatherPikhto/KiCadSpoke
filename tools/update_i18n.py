#!/usr/bin/env python
"""
Обновление файлов перевода gettext.
Запускать из корня проекта: python tools/update_i18n.py
Требуется установленный Babel (pip install babel).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def run(cmd):
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def main():
    # 1. Извлечение строк в .pot
    run(["pybabel", "extract", "-F", "babel.cfg", "-o", "messages.pot", "."])

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