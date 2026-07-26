#!/usr/bin/env python3
"""
repeat_first_write_crash.py — гоняет N попыток "первая транзакция после
чистого старта KiCad" подряд и считает процент падений.

ЗАЧЕМ: баг #24966 (checkForBusy/m_frame null) интермиттентный — сам
оригинальный отчёт отмечает "on a freshly rebooted Windows it reproduces
reliably; on a warmed-up system it becomes intermittent". Один прогон
(упало/не упало) статистически ничего не доказывает — ни для проверки
самого бага, ни для проверки гипотетических воркэраундов. Нужна серия
одинаковых попыток и доля падений, не единичный анекдот.

Цикл на итерацию:
  1. Убить KiCad (если запущен) — pkill -x kicad.
  2. Почистить хвосты — utils/clean_kicad_crash_state.py.
  3. Запустить KiCad с указанным проектом (flatpak run ... <project>).
  4. Подождать готовности IPC (ping с ретраями).
  5. Одна no-op транзакция: begin_commit -> update_items -> push_commit
     (ровно боевой путь adapter.commit_with_retry, не голый update_items —
     см. diagnose_first_write_crash.py и разбор 2026-07-26).
  6. Записать: упало или нет (по ConnectionError/обрыву соединения).
  7. Убить KiCad, следующая итерация.

ВАЖНО: "Schematic Editor открыт в сессии" — условие из issue #24966.
Открывается ли он автоматически при запуске проекта — зависит от того,
запомнил ли сам ПРОЕКТ открытые окна с прошлого раза. Если нет — открой
Schematic Editor руками один раз и сохрани проект (File -> Save Project),
тогда KiCad будет переоткрывать его сам при следующих стартах цикла.

Запуск:
  python utils/repeat_first_write_crash.py --project test_boards/3CH-AWG-TIA/3CH-AWG-TIA.kicad_pro --runs 10
  python utils/repeat_first_write_crash.py --project <...> --runs 10 --settle-delay 30   # тест гипотезы H1
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

FLATPAK_APP_ID = "org.kicad.KiCad"


def kill_kicad():
    subprocess.run(["pkill", "-x", "kicad"], capture_output=True)


def clean_state(boards_dir: Path):
    subprocess.run(
        [sys.executable, "utils/clean_kicad_crash_state.py", "--boards-dir", str(boards_dir)],
        capture_output=True,
    )


def launch_kicad(project_path: str):
    subprocess.Popen(
        ["flatpak", "run", FLATPAK_APP_ID, project_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def wait_for_ipc(timeout_s: float) -> bool:
    """
    ВАЖНО: одного ping() недостаточно — он отвечает, как только поднялся
    сам KiCad-процесс с базовым API-сервером, ЗАДОЛГО до того, как
    pcbnew реально загрузит плату и зарегистрирует свой обработчик.
    Если стучаться раньше — get_board() падает с
    'ApiError: no handler available for request of type ...
    GetOpenDocuments' — это НЕ баг #24966 (тот рвёт соединение, а не
    отвечает вежливым ApiError), а гонка готовности. Поэтому ждём именно
    get_board(), а не только ping().
    """
    import kipy
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            k = kipy.KiCad(timeout_ms=2000)
            k.ping()
            k.get_board()
            return True
        except Exception:
            time.sleep(1.0)
    return False


def try_commit_once() -> bool:
    """True = транзакция прошла без падения, False = упало."""
    import kipy
    try:
        k = kipy.KiCad(timeout_ms=15000)
        board = k.get_board()
        fps = list(board.get_footprints())
        if not fps:
            print("  [предупреждение] на плате нет футпринтов — тест невозможен, считаю OK")
            return True
        fp = fps[0]
        commit = board.begin_commit()
        board.update_items([fp])
        board.push_commit(commit, "repeat_first_write_crash: no-op")
        return True
    except Exception as e:
        print(f"  -> упало: {type(e).__name__}: {e}")
        return False


def main():
    ap = argparse.ArgumentParser(description="Серия попыток first-write краша для оценки доли падений")
    ap.add_argument("--project", required=True, help="Путь к .kicad_pro для запуска")
    ap.add_argument("--runs", type=int, default=10, help="Сколько итераций (по умолчанию 10)")
    ap.add_argument("--boards-dir", default="test_boards", help="Для clean_kicad_crash_state.py")
    ap.add_argument("--startup-wait", type=float, default=30.0, help="Таймаут ожидания готовности IPC, с")
    ap.add_argument("--settle-delay", type=float, default=0.0,
                     help="Пауза после готовности IPC перед транзакцией (тест гипотезы H1 о гонке)")
    args = ap.parse_args()

    results = []
    for i in range(1, args.runs + 1):
        print(f"=== попытка {i}/{args.runs} ===")
        kill_kicad()
        time.sleep(1.0)
        clean_state(Path(args.boards_dir))
        launch_kicad(args.project)

        if not wait_for_ipc(args.startup_wait):
            print("  -> KiCad не поднялся за отведённое время, пропуск")
            results.append(None)
            continue

        if args.settle_delay > 0:
            time.sleep(args.settle_delay)

        ok = try_commit_once()
        results.append(ok)
        print(f"  -> {'OK' if ok else 'CRASH'}")

    kill_kicad()

    total = len([r for r in results if r is not None])
    crashes = len([r for r in results if r is False])
    skipped = results.count(None)
    print()
    print("===== ИТОГ =====")
    print(f"Прогонов: {total} (пропущено из-за таймаута старта: {skipped})")
    if total:
        print(f"Падений: {crashes} ({crashes / total * 100:.0f}%)")
    else:
        print("Падений: н/д (ни один прогон не поднялся)")


if __name__ == "__main__":
    main()
