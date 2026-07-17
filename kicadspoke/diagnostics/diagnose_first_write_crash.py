# kicadspoke/diagnostics/diagnose_first_write_crash.py
"""
diagnose_first_write_crash.py — воспроизводимая лесенка для локализации
падения KiCad на первой записи после старта.

Гипотезы, которые различает скрипт (см. разговор 2026-07-17):
  H1. Гонка первой записи с ленивой инициализацией KiCad:
      чтения (ступени 1-8) переживаются, умирает ровно WRITE (ступень 9),
      причём только на свежем инстансе; --delay N сдвигает/лечит.
  H2. Зомби-инстанс от прошлой сессии: в снапшоте окружения видно >1
      kicad.exe ИЛИ KICAD_API_TOKEN/KICAD_API_SOCKET торчат из прошлой
      сессии; после смерти "нашего" собеседника PID другого жив.
  H3. Падение не привязано к записи: умирает уже на чтениях.

Запуск (KiCad открыт, плата загружена):
  python -m kicadspoke.diagnostics.diagnose_first_write_crash
  python -m kicadspoke.diagnostics.diagnose_first_write_crash --until 8   # только чтения
  python -m kicadspoke.diagnostics.diagnose_first_write_crash --delay 30  # пауза перед записью
  python -m kicadspoke.diagnostics.diagnose_first_write_crash --repeat 3  # повторить запись

Каждая ступень: замер времени, вердикт OK/FAIL, после — ping и сверка
списка PID kicad.exe. Лог пишется В ФАЙЛ ПОСТРОЧНО С FLUSH — даже если
всё умрёт, последняя строка честно скажет, где именно.
"""

import argparse
import datetime
import logging
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

logger = logging.getLogger("diagnose")


# ---------------------------------------------------------------- утилиты

class FlushingFileHandler(logging.FileHandler):
    """Flush после каждой записи — лог обязан пережить смерть чего угодно."""
    def emit(self, record):
        super().emit(record)
        self.flush()


def setup_logging(log_path: Path):
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)s: %(message)s",
                            datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fh = FlushingFileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(ch)
    # pynng шумит полезным: Pipe callback event 2 = обрыв пайпа = смерть сервера
    logging.getLogger("pynng").setLevel(logging.DEBUG)
    logging.getLogger("kipy").setLevel(logging.DEBUG)


def list_kicad_pids():
    """PID всех kicad.exe (Windows: tasklist; иначе psutil, если есть)."""
    pids = []
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq kicad.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in out.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 2 and parts[0].lower() == "kicad.exe":
                    pids.append(int(parts[1]))
        else:
            import psutil  # опционально
            pids = [p.pid for p in psutil.process_iter(["name"])
                    if p.info["name"] and "kicad" in p.info["name"].lower()]
    except Exception as e:
        logger.debug(f"не удалось получить PID kicad: {e}")
    return sorted(pids)


def list_socket_candidates():
    """
    Кандидаты в API-сокеты. На Windows сокет лежит в подкаталоге %TEMP%;
    у первого инстанса имя api.sock, у последующих api-<PID>.sock —
    несколько файлов = след зомби-инстансов.
    """
    found = []
    roots = [Path(tempfile.gettempdir())]
    env_sock = os.environ.get("KICAD_API_SOCKET")
    if env_sock:
        p = env_sock.replace("ipc://", "")
        found.append(f"(из env) {p} exists={Path(p).exists()}")
    for root in roots:
        try:
            for p in root.rglob("api*.sock"):
                st = p.stat()
                age = time.time() - st.st_mtime
                found.append(f"{p} (mtime {age/60:.1f} мин назад)")
        except Exception as e:
            logger.debug(f"обход {root}: {e}")
    return found


def snapshot_environment(tag: str):
    logger.info(f"--- снапшот окружения [{tag}] ---")
    for var in ("KICAD_API_SOCKET", "KICAD_API_TOKEN"):
        val = os.environ.get(var)
        logger.info(f"  {var} = {val!r}"
                    + ("  <-- ТОРЧИТ ИЗ ОКРУЖЕНИЯ (кандидат в stale!)" if val else ""))
    pids = list_kicad_pids()
    logger.info(f"  kicad.exe PID: {pids or 'не найдено'}"
                + ("  <-- БОЛЬШЕ ОДНОГО ИНСТАНСА (гипотеза H2: зомби!)" if len(pids) > 1 else ""))
    for s in list_socket_candidates():
        logger.info(f"  сокет: {s}")
    return pids


# ---------------------------------------------------------------- лесенка

class Ladder:
    def __init__(self, baseline_pids):
        self.kicad = None
        self.board = None
        self.fp = None
        self.baseline_pids = baseline_pids
        self.results = []  # (номер, имя, вердикт, длительность)

    def step(self, num, name, fn, check_pulse=True):
        logger.info(f"===== СТУПЕНЬ {num}: {name} =====")
        t0 = time.perf_counter()
        try:
            out = fn()
            dt = time.perf_counter() - t0
            logger.info(f"ступень {num} OK за {dt:.3f} с" + (f": {out}" if out else ""))
            self.results.append((num, name, "OK", dt))
            ok = True
        except BaseException as e:
            dt = time.perf_counter() - t0
            logger.error(f"ступень {num} FAIL за {dt:.3f} с: {type(e).__name__}: {e}")
            logger.debug(traceback.format_exc())
            self.results.append((num, name, f"FAIL: {type(e).__name__}", dt))
            ok = False
        if check_pulse:
            self.pulse(num)
        return ok

    def pulse(self, after_step):
        """Пульс: ping + сверка PID. Здесь ловится сам факт смерти KiCad."""
        pids = list_kicad_pids()
        died = [p for p in self.baseline_pids if p not in pids]
        if died:
            logger.error(f"!!! kicad.exe PID {died} УМЕР после ступени {after_step} !!!")
        if pids and set(pids) != set(self.baseline_pids):
            logger.warning(f"состав PID изменился: было {self.baseline_pids}, стало {pids}")
        if self.kicad is not None:
            try:
                t0 = time.perf_counter()
                self.kicad.ping()
                logger.info(f"пульс после ступени {after_step}: ping OK "
                            f"({(time.perf_counter()-t0)*1000:.0f} мс), PID {pids}")
            except BaseException as e:
                logger.error(f"пульс после ступени {after_step}: ping FAIL — "
                             f"{type(e).__name__}: {e}; PID {pids}")

    # --- содержимое ступеней ---

    def s_connect(self, timeout_ms):
        import kipy
        self.kicad = kipy.KiCad(timeout_ms=timeout_ms)
        return "клиент создан"

    def s_ping(self):
        self.kicad.ping()
        return "pong"

    def s_version(self):
        v = self.kicad.get_version()
        try:
            api_v = self.kicad.get_api_version()
        except Exception as e:
            api_v = f"<{type(e).__name__}>"
        return f"kicad={v}, api={api_v}"

    def s_documents(self):
        from kipy.proto.common.types import DocumentType
        docs = self.kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
        return f"открыто PCB-документов: {len(docs)}"

    def s_board(self):
        self.board = self.kicad.get_board()
        return f"board получен: {self.board is not None}"

    def s_read_footprints(self):
        fps = list(self.board.get_footprints())
        if not fps:
            raise RuntimeError("на плате нет футпринтов — записывать нечего")
        # Кандидат для no-op записи: маленький пассив (C*/R*), не FPGA —
        # чтобы даже теоретическая порча затронула минимум.
        self.fp = next((f for f in fps
                        if f.reference_field.text.value[:1] in ("C", "R")), fps[0])
        return f"{len(fps)} футпринтов; кандидат для записи: " \
               f"{self.fp.reference_field.text.value}"

    def s_deep_read(self):
        fp = self.fp
        from kipy.board_types import Pad
        pads = [i for i in fp.definition.items if isinstance(i, Pad)]
        return (f"{fp.reference_field.text.value}: pos=({fp.position.x/1e6:.3f}, "
                f"{fp.position.y/1e6:.3f}) мм, angle={fp.orientation.degrees:.1f}, "
                f"layer={fp.layer}, падов={len(pads)}")

    def s_noop_write(self):
        """
        ПОДОЗРЕВАЕМЫЙ: board.update_items([fp]) БЕЗ каких-либо изменений.
        Ровно тот вызов, на котором падало (adapter.update_items ->
        board.update_items). Если умирает здесь — минимальная репродукция
        для issue готова: «no-op update_items на свежем инстансе».
        """
        ref = self.fp.reference_field.text.value
        logger.info(f"отправляю no-op update_items([{ref}])...")
        self.board.update_items([self.fp])
        return f"no-op запись {ref} прошла"


def main():
    ap = argparse.ArgumentParser(description="Диагностика падения KiCad на первой записи")
    ap.add_argument("--log", default=None, help="путь к лог-файлу")
    ap.add_argument("--until", type=int, default=9,
                    help="выполнить ступени до N включительно (8 = только чтения)")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="пауза (сек) ПЕРЕД первой записью — тест гипотезы H1")
    ap.add_argument("--repeat", type=int, default=1,
                    help="сколько раз повторить no-op запись")
    ap.add_argument("--timeout-ms", type=int, default=15000)
    args = ap.parse_args()

    log_path = Path(args.log) if args.log else Path(
        f"diag_{datetime.datetime.now():%Y%m%d_%H%M%S}.log")
    setup_logging(log_path)
    logger.info(f"лог: {log_path.resolve()}")
    logger.info(f"python {sys.version.split()[0]}; аргументы: {vars(args)}")
    try:
        import kipy
        logger.info(f"kipy {getattr(kipy, '__version__', '?')}")
    except ImportError:
        logger.error("kipy не установлен")
        return 2

    baseline = snapshot_environment("до подключения")
    ladder = Ladder(baseline)

    steps = [
        (1, "connect (kipy.KiCad)", lambda: ladder.s_connect(args.timeout_ms)),
        (2, "ping", ladder.s_ping),
        (3, "get_version/get_api_version", ladder.s_version),
        (4, "get_open_documents(PCB)", ladder.s_documents),
        (5, "get_board", ladder.s_board),
        (6, "чтение футпринтов", ladder.s_read_footprints),
        (7, "глубокое чтение одного футпринта", ladder.s_deep_read),
        (8, "повторное чтение (стабильность чтений)", ladder.s_read_footprints),
    ]

    for num, name, fn in steps:
        if num > args.until:
            break
        if not ladder.step(num, name, fn):
            logger.error(f"лесенка оборвалась на ступени {num} ({name}) — см. вердикт выше")
            break
    else:
        if args.until >= 9:
            if args.delay > 0:
                logger.info(f"пауза {args.delay} с перед записью (тест H1)...")
                time.sleep(args.delay)
            for i in range(args.repeat):
                tag = f"9.{i+1}" if args.repeat > 1 else "9"
                if not ladder.step(tag, "NO-OP WRITE: update_items([fp]) без изменений",
                                   ladder.s_noop_write):
                    break
                time.sleep(0.5)

    snapshot_environment("после лесенки")
    logger.info("===== ИТОГ =====")
    for num, name, verdict, dt in ladder.results:
        logger.info(f"  [{num}] {name}: {verdict} ({dt:.3f} с)")
    logger.info(f"полный лог: {log_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
