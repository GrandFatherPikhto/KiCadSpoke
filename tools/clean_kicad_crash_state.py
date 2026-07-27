#!/usr/bin/env python3
"""
clean_kicad_crash_state.py — чистит "хвосты" от рухнувшей сессии KiCad
(Flatpak на Linux), которые мешают следующему запуску начать с чистого
листа: см. разбор 2026-07-26 (стал core_pattern/gdb, api.sock не убрался
после сегфолта).

НЕ трогает ничего, если KiCad сейчас запущен (проверяет pgrep) — иначе
можно выдернуть сокет из-под живой сессии.

Чистит:
  1. Протухший IPC-сокет/лок Flatpak-версии
     (~/.var/app/org.kicad.KiCad/cache/tmp/kicad/{api.sock,api.lock}) —
     остаётся, если процесс умер (segfault), не закрывшись штатно.
  2. Протухшие записи в cache/tmp/org.kicad.kicad/instances/.
  3. Project lock-файлы (*.lck) в указанной директории плат (по
     умолчанию test_boards/ в текущей директории — запускать из корня
     репозитория, как и остальные скрипты в tools/).

Запуск:
  python tools/clean_kicad_crash_state.py
  python tools/clean_kicad_crash_state.py --dry-run
  python tools/clean_kicad_crash_state.py --boards-dir path/to/boards
"""
import argparse
import subprocess
import sys
from pathlib import Path
from typing import List

FLATPAK_APP_ID = "org.kicad.KiCad"


def kicad_is_running() -> bool:
    """
    ВАЖНО: -x (точное имя процесса), не -f (командная строка) — иначе
    pgrep ловит сам этот скрипт (его имя файла тоже содержит "kicad").
    """
    try:
        result = subprocess.run(["pgrep", "-x", "kicad"], capture_output=True, text=True)
        return bool(result.stdout.strip())
    except FileNotFoundError:
        print("[предупреждение] pgrep не найден — не могу проверить, запущен ли KiCad; "
              "пропускаю чистку на всякий случай", file=sys.stderr)
        return True


def find_targets(boards_dir: Path) -> List[Path]:
    app_data = Path.home() / ".var" / "app" / FLATPAK_APP_ID

    targets = [
        app_data / "cache" / "tmp" / "kicad" / "api.sock",
        app_data / "cache" / "tmp" / "kicad" / "api.lock",
    ]

    instances_dir = app_data / "cache" / "tmp" / "org.kicad.kicad" / "instances"
    if instances_dir.is_dir():
        targets.extend(p for p in instances_dir.iterdir() if p.is_file())

    if boards_dir.is_dir():
        targets.extend(boards_dir.rglob("*.lck"))

    return [p for p in targets if p.exists()]


def main():
    ap = argparse.ArgumentParser(description="Чистка хвостов от рухнувшей сессии KiCad (Flatpak/Linux)")
    ap.add_argument("--dry-run", action="store_true", help="Только показать, что будет удалено")
    ap.add_argument("--boards-dir", default="test_boards",
                     help="Где искать *.lck (по умолчанию test_boards/ в текущей директории)")
    args = ap.parse_args()

    if kicad_is_running():
        sys.exit("[ошибка] KiCad сейчас запущен — не трогаю IPC-сокет живой сессии. "
                 "Закрой KiCad и запусти скрипт снова.")

    targets = find_targets(Path(args.boards_dir))
    if not targets:
        print("Нечего чистить — хвостов не найдено.")
        return

    for path in targets:
        if args.dry_run:
            print(f"[dry-run] удалил бы: {path}")
        else:
            path.unlink()
            print(f"удалено: {path}")


if __name__ == "__main__":
    main()
