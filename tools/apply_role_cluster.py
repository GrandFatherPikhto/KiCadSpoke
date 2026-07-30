#!/usr/bin/env python3
"""
apply_role_cluster.py — массовая простановка полей Role/Cluster на
символы схемы (.kicad_sch) по YAML-конфигу (refdes -> {Role: ..., Cluster: ...}).

Повод: Bulk Edit в Eeschema багованный (крашится, требует двойного
ввода — см. techdocs/status), а KiCadStamp не пишет в .kicad_sch
ВООБЩЕ НИ РАЗУ за всю историю (проверено 2026-07-26) — это принципиально
другая зона риска, чем правка PCB через kipy: там за нас всё держит
транзакция/undo самого KiCad, тут — ничего подобного, и нет прецедента,
что sexpdata.dumps() воспроизводит форматирование KiCad 1:1 обратно.
Поэтому:

  - Правки — ТОЧЕЧНАЯ ПОДСТАНОВКА ТЕКСТА (regex + баланс скобок для
    поиска границ blocks/значений), не полный parse->dump цикл. Трогаем
    только байты внутри значения нужного property (или точку вставки
    нового) — всё остальное содержимое файла остаётся байт-в-байт
    прежним. sexpdata используется только для самопроверки результата,
    не для чтения/записи основного содержимого.
  - dry-run по умолчанию — без --write только печатает, что изменится.
  - --write делает <file>.bak КАЖДОГО затрагиваемого файла ПЕРЕД правкой
    и сразу перепарсивает получившийся файл через sexpdata как
    самопроверку (round-trip); если не парсится — автоматически
    восстанавливает .bak и репортит ошибку именно по этому файлу,
    остальные файлы обрабатываются независимо.
  - Отказывается писать, если хоть одно значение в конфиге содержит
    не-ASCII символ (см. diagnostic_charset.py — ровно тот класс
    опечатки, из-за которого этот скрипт появился) — без --allow-non-ascii.
  - Отказывается писать, если обнаружен запущенный KiCad (тот же
    кросс-платформенный PID-снимок, что в diagnose_first_write_crash.py)
    — правим файл в обход живой сессии, значит сессии быть не должно.

Два способа, которыми один refdes встречается в файле, и оба учтены:
  - Многоюнитовый символ (сдвоенный ОУ и т.п.) — один refdes сидит в
    НЕСКОЛЬКИХ отдельных (symbol ...) блоках (по одному на unit), у
    каждого своя копия Role/Cluster — правятся ВСЕ блоки этого refdes.
  - Многократный инстанс одного листа (Channel_1/2/3 из одного
    channel_tpl.kicad_sch) — НЕСКОЛЬКО refdes сидят в ОДНОМ (symbol
    ...) блоке (Role/Cluster общие на весь блок, см. (instances ...)).
    Если конфиг просит РАЗНЫЕ значения для двух таких refdes — формат
    физически не может это выразить, скрипт фатально откажется, а не
    молча применит одно из двух.

Формат конфига:
    schematic_dir: ../test_boards/3CH-AWG-TIA   # относительно этого YAML
    fields:
      C51:
        Role: C_OUT_BULK
        Cluster: FPGA_PWR_BANK/17
      C52:
        Role: C_OUT_BULK
        Cluster: FPGA_PWR_BANK/26

Запуск:
    python utils/apply_role_cluster.py roles.yaml                # dry-run
    python utils/apply_role_cluster.py roles.yaml --write        # реально пишет
    python utils/apply_role_cluster.py roles.yaml --write --allow-non-ascii
    python utils/apply_role_cluster.py roles.yaml --write --force-with-kicad-running
"""
import argparse
import difflib
import glob
import logging
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sexpdata
import yaml

logger = logging.getLogger(__name__)

PROPERTY_BLOCK_TEMPLATE = (
    '{prefix}(property "{field}" "{value}"\r\n'
    '{prefix}\t(at {x} {y} 0)\r\n'
    '{prefix}\t(hide yes)\r\n'
    '{prefix}\t(show_name no)\r\n'
    '{prefix}\t(do_not_autoplace no)\r\n'
    '{prefix}\t(effects\r\n'
    '{prefix}\t\t(font\r\n'
    '{prefix}\t\t\t(size 1.27 1.27)\r\n'
    '{prefix}\t\t)\r\n'
    '{prefix}\t)\r\n'
    '{prefix})\r\n'
)


# --------------------------------------------------------------------------
# ASCII-проверка (та же логика, что diagnostic_charset.py — не импортируем
# оттуда, чтобы не тянуть kipy в скрипт, который вообще не подключается к
# KiCad; полсотни строк дублирования дешевле лишней зависимости)
# --------------------------------------------------------------------------

def find_non_ascii(value: str) -> List[Tuple[int, str, int, str]]:
    bad = []
    for i, ch in enumerate(value):
        if not (0x20 <= ord(ch) <= 0x7E):
            try:
                name = unicodedata.name(ch)
            except ValueError:
                name = "БЕЗ ИМЕНИ"
            bad.append((i, ch, ord(ch), name))
    return bad


def list_kicad_pids() -> List[int]:
    """Тот же снимок, что в diagnose_first_write_crash.py — если KiCad
    запущен, файл потенциально открыт в Eeschema, и правка мимо живой
    сессии может быть тихо затёрта следующим сохранением в KiCad."""
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
            import psutil  # опционально, см. requirements.txt
            pids = [p.pid for p in psutil.process_iter(["name"])
                    if p.info["name"] and "kicad" in p.info["name"].lower()]
    except Exception as e:
        logger.debug(f"не удалось получить PID kicad: {e}")
    return sorted(pids)


# --------------------------------------------------------------------------
# Локализация блоков (symbol ...) и полей внутри них — точечный разбор
# текста, не sexpdata: нужны byte-offset'ы для точечной подстановки.
# --------------------------------------------------------------------------

def find_balanced_span(text: str, open_idx: int) -> int:
    """text[open_idx] должен быть '(' — возвращает индекс СРАЗУ ПОСЛЕ
    соответствующей ')', с учётом строк в кавычках (KiCad экранирует
    кавычку внутри строки как \\")."""
    assert text[open_idx] == '('
    depth = 0
    i = open_idx
    in_string = False
    n = len(text)
    while i < n:
        c = text[i]
        if in_string:
            if c == '\\':
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError(f"несбалансированные скобки начиная с позиции {open_idx}")


class SymbolBlock:
    __slots__ = ("file", "start", "end", "refs")

    def __init__(self, file, start, end, refs):
        self.file = file
        self.start = start
        self.end = end
        self.refs = refs  # set of refdes из (instances ...)

    @property
    def id(self):
        return (self.file, self.start)


def iter_symbol_blocks(path: str, text: str) -> List[SymbolBlock]:
    """Все блоки РАЗМЕЩЁННЫХ инстансов символов — НЕ определения в
    lib_symbols. Разделение проверено на реальных файлах, 100% без
    исключений: определения в lib_symbols — всегда `(symbol "Lib:Name"
    ...)` (имя сразу после "symbol"), у размещённых инстансов сразу за
    "symbol" идёт перевод строки, имени там нет вообще."""
    blocks = []
    for m in re.finditer(r'\(symbol\r?\n', text):
        start = m.start()
        end = find_balanced_span(text, start)
        span_text = text[start:end]
        instances_m = re.search(r'\(instances\r?\n', span_text)
        refs = set()
        if instances_m:
            inst_start = instances_m.start()
            inst_end = find_balanced_span(span_text, inst_start)
            for ref_m in re.finditer(r'\(reference "((?:[^"\\]|\\.)*)"',
                                      span_text[inst_start:inst_end]):
                refs.add(ref_m.group(1))
        blocks.append(SymbolBlock(path, start, end, refs))
    return blocks


def find_property_value_span(span_text: str, field: str) -> Optional[Tuple[int, int]]:
    """(start, end) значения (внутри кавычек, относительно span_text) для
    (property "{field}" "..." ...), либо None, если такого property нет
    в этом блоке."""
    m = re.search(r'\(property "' + re.escape(field) + r'" "((?:[^"\\]|\\.)*)"', span_text)
    if not m:
        return None
    return m.start(1), m.end(1)


def find_symbol_at(span_text: str) -> Tuple[str, str]:
    """(x, y) из собственного (at X Y ROT) символа — первый (at ...) в
    блоке, до любых (property ...); скрытые bookkeeping-поля вроде
    Role/Cluster в реальных файлах всегда используют координаты
    символа, а не какое-то отдельное смещение."""
    m = re.search(r'\(at (-?[0-9.]+) (-?[0-9.]+)', span_text)
    if not m:
        raise ValueError("не найден (at ...) у символа")
    return m.group(1), m.group(2)


def find_insertion_point(span_text: str) -> Tuple[int, str]:
    """(индекс в span_text, отступ) для вставки нового (property ...) —
    перед первым (pin ...), либо перед (instances ...), если пинов нет
    вообще (страховка, в реальных файлах такого не встречалось)."""
    m = re.search(r'\n([ \t]*)\(pin ', span_text)
    if not m:
        m = re.search(r'\n([ \t]*)\(instances\r?\n', span_text)
    if not m:
        raise ValueError("не найдена точка вставки ((pin ...) и (instances ...) отсутствуют)")
    return m.start() + 1, m.group(1)


def escape_sexp_string(value: str) -> str:
    return value.replace('\\', '\\\\').replace('"', '\\"')


# --------------------------------------------------------------------------
# Применение правок текстом (без сериализации всего файла)
# --------------------------------------------------------------------------

def apply_edits(text: str, edits: List[Tuple[int, int, str]]) -> str:
    """edits — [(start, end, replacement), ...], необязательно
    отсортированные; несколько вставок в одну и ту же точку (start==end)
    допустимы, порядок между ними сохраняется как в исходном списке."""
    ordered = sorted(range(len(edits)), key=lambda i: (edits[i][0], edits[i][1], i))
    out = []
    cursor = 0
    for idx in ordered:
        start, end, repl = edits[idx]
        if start < cursor:
            raise ValueError("пересекающиеся правки — это баг скрипта, а не конфига")
        out.append(text[cursor:start])
        out.append(repl)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


# --------------------------------------------------------------------------

def load_config(path: Path):
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    schematic_dir = data.get('schematic_dir')
    if not schematic_dir:
        sys.exit("[ошибка] в конфиге нет schematic_dir")
    fields = data.get('fields') or {}
    if not fields:
        sys.exit("[ошибка] в конфиге пуст (или отсутствует) fields:")
    return schematic_dir, fields


def main():
    ap = argparse.ArgumentParser(
        description="Массовая простановка Role/Cluster в .kicad_sch по YAML-конфигу "
                     "(refdes -> {Role, Cluster}). По умолчанию — dry-run.")
    ap.add_argument("config", help="YAML: schematic_dir + fields")
    ap.add_argument("--write", action="store_true", help="реально записать (иначе только dry-run)")
    ap.add_argument("--allow-non-ascii", action="store_true",
                     help="не проверять значения на не-ASCII символы (не рекомендуется)")
    ap.add_argument("--force-with-kicad-running", action="store_true",
                     help="писать, даже если обнаружен запущенный процесс KiCad")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    config_path = Path(args.config)
    schematic_dir, fields_cfg = load_config(config_path)

    # --- 1. ASCII-проверка значений конфига (до чтения схемы вообще) ---
    bad_values = []
    for ref, fv in fields_cfg.items():
        for field, value in fv.items():
            bad = find_non_ascii(str(value))
            if bad:
                bad_values.append((ref, field, value, bad))
    if bad_values and not args.allow_non_ascii:
        print(f"[ФАТАЛ] {len(bad_values)} значение(й) в конфиге содержит не-ASCII символы — "
              f"ровно тот класс опечатки (гомоглиф из другого алфавита), из-за которого "
              f"этот скрипт появился (см. diagnostic_charset.py). Ничего не записано.\n")
        for ref, field, value, bad in bad_values:
            print(f"  {ref}.{field} = {value!r}")
            for i, ch, cp, name in bad:
                print(f"      позиция {i}: {ch!r} U+{cp:04X} ({name})")
        print("\nЕсли не-ASCII символы там осознанно — повторите с --allow-non-ascii.")
        sys.exit(1)

    # --- 2. Собираем *.kicad_sch (как sheet_names.py — не рекурсивно) ---
    base = config_path.parent
    sch_dir = base / schematic_dir
    if not sch_dir.is_dir():
        sys.exit(f"[ошибка] schematic_dir {schematic_dir!r} не найден ({sch_dir})")
    files = sorted(
        p for p in glob.glob(str(sch_dir / "*.kicad_sch"))
        if not Path(p).name.startswith("_autosave")
    )
    if not files:
        sys.exit(f"[ошибка] в {sch_dir} нет ни одного .kicad_sch")
    logger.info(f"найдено {len(files)} файлов .kicad_sch в {sch_dir}")

    # --- 3. Парсим все файлы, строим refdes -> [SymbolBlock, ...] ---
    file_texts: Dict[str, str] = {}
    all_blocks: List[SymbolBlock] = []
    for f in files:
        with open(f, encoding='utf-8', newline='') as fh:
            file_texts[f] = fh.read()
        blocks = iter_symbol_blocks(f, file_texts[f])
        all_blocks.extend(blocks)
        logger.debug(f"{Path(f).name}: {len(blocks)} символов размещено")

    ref_to_blocks: Dict[str, List[SymbolBlock]] = {}
    for b in all_blocks:
        for r in b.refs:
            ref_to_blocks.setdefault(r, []).append(b)

    # --- 4. Разрешаем refdes из конфига ---
    missing = [r for r in fields_cfg if r not in ref_to_blocks]
    if missing:
        known = sorted(ref_to_blocks.keys())
        lines = []
        for r in missing:
            suggestion = difflib.get_close_matches(r, known, n=1)
            hint = f" (может, имелся в виду {suggestion[0]!r}?)" if suggestion else ""
            lines.append(f"  {r} — не найден ни в одном .kicad_sch из {sch_dir}{hint}")
        sys.exit("[ошибка] неизвестные refdes в конфиге:\n" + "\n".join(lines))

    # --- 5. Мержим запросы по блокам + ловим конфликты общих инстансов ---
    blocks_by_id = {b.id: b for b in all_blocks}
    # bid -> {field: (value, refdes_первым_попросивший)}
    blocks_fields: Dict[Tuple[str, int], Dict[str, Tuple[str, str]]] = {}
    conflicts = []
    for ref, fv in fields_cfg.items():
        for b in ref_to_blocks[ref]:
            per_block = blocks_fields.setdefault(b.id, {})
            for field, value in fv.items():
                value = str(value)
                if field in per_block and per_block[field][0] != value:
                    conflicts.append((b, field, per_block[field][1], per_block[field][0], ref, value))
                else:
                    per_block[field] = (value, ref)
    if conflicts:
        lines = []
        for b, field, ref_a, val_a, ref_b, val_b in conflicts:
            lines.append(
                f"  {Path(b.file).name}: {ref_a!r} и {ref_b!r} — общий (symbol ...) "
                f"(многократный инстанс одного листа), но просите разные {field}: "
                f"{val_a!r} vs {val_b!r}"
            )
        sys.exit(
            "[ошибка] конфликт: Role/Cluster общие на весь (symbol ...) при многократном "
            "инстансировании одного листа — .kicad_sch физически не может дать разным "
            "инстансам разные значения одного и того же символа:\n" + "\n".join(lines)
        )

    # --- 6. Планируем правки (по одной на уникальный блок+поле) ---
    edits_by_file: Dict[str, List[Tuple[int, int, str]]] = {f: [] for f in files}
    report = []  # (file, refs, field, old_or_None, new, kind)
    for bid, per_block in blocks_fields.items():
        b = blocks_by_id[bid]
        span_text = file_texts[b.file][b.start:b.end]
        for field, (value, _ref) in per_block.items():
            escaped = escape_sexp_string(value)
            existing = find_property_value_span(span_text, field)
            if existing:
                vs, ve = existing
                old_value = span_text[vs:ve]
                if old_value == escaped:
                    continue  # уже стоит верно
                edits_by_file[b.file].append((b.start + vs, b.start + ve, escaped))
                report.append((b.file, sorted(b.refs), field, old_value, value, "заменить"))
            else:
                insert_at, prefix = find_insertion_point(span_text)
                x, y = find_symbol_at(span_text)
                block_text = PROPERTY_BLOCK_TEMPLATE.format(
                    prefix=prefix, field=field, value=escaped, x=x, y=y)
                edits_by_file[b.file].append((b.start + insert_at, b.start + insert_at, block_text))
                report.append((b.file, sorted(b.refs), field, None, value, "добавить"))

    if not report:
        print("Нечего менять — все запрошенные Role/Cluster уже стоят как в конфиге.")
        return 0

    print(f"\n=== {'ЗАПИСЬ' if args.write else 'DRY-RUN'}: {len(report)} правк(и/у) ===\n")
    for file, refs, field, old, new, kind in sorted(report, key=lambda r: (r[0], r[1])):
        refs_s = ",".join(refs)
        if kind == "заменить":
            print(f"  [{Path(file).name}] {refs_s} .{field}: {old!r} -> {new!r}")
        else:
            print(f"  [{Path(file).name}] {refs_s} .{field}: (нет поля) -> {new!r}  [новое свойство]")

    if not args.write:
        print("\nDry-run — ничего не записано. Повторите с --write, чтобы применить.")
        return 0

    # --- 7. Проверка на живой KiCad ---
    pids = list_kicad_pids()
    if pids and not args.force_with_kicad_running:
        sys.exit(
            f"[ошибка] обнаружен запущенный KiCad (PID {pids}) — файл может быть открыт "
            f"в Eeschema, правка в обход живой сессии рискует быть тихо затёртой следующим "
            f"сохранением в KiCad. Закрой KiCad и повтори, либо (если точно знаешь, что этот "
            f"файл нигде не открыт) --force-with-kicad-running."
        )

    # --- 8. Запись: .bak -> splice -> самопроверка sexpdata, по файлу ---
    failed = []
    written = []
    for file, edits in edits_by_file.items():
        if not edits:
            continue
        bak_path = file + ".bak"
        with open(file, encoding='utf-8', newline='') as fh:
            original = fh.read()
        with open(bak_path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(original)
        new_text = apply_edits(original, edits)
        with open(file, 'w', encoding='utf-8', newline='') as fh:
            fh.write(new_text)
        try:
            with open(file, encoding='utf-8') as fh:
                sexpdata.load(fh)
        except Exception as e:
            logger.error(f"{file}: результат не парсится sexpdata ({type(e).__name__}: {e}) "
                         f"— восстанавливаю из {bak_path}")
            with open(file, 'w', encoding='utf-8', newline='') as fh:
                fh.write(original)
            failed.append(file)
            continue
        written.append(file)
        logger.info(f"{file}: записано, бэкап в {bak_path}, самопроверка sexpdata — ок")

    print(f"\nЗаписано файлов: {len(written)}. Бэкапы — рядом, с расширением .bak.")
    if failed:
        print(f"НЕ УДАЛОСЬ записать (восстановлено из .bak): {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
