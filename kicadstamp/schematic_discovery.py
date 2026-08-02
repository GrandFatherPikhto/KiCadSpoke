# kicadstamp/schematic_discovery.py
"""
walk_schematic_hierarchy() — finds every .kicad_sch reachable from one
root sheet, by following (sheet (property "Sheetfile" "...") ...)
references recursively. Replaces the flat schematic_dir glob
tools/apply_role_cluster.py (and kicadstamp/sheet_names.py) use — that
only works because this project happens to keep every subsystem
.kicad_sch as siblings in one folder; a GUI needs to pick a PROJECT (one
root sheet), not a folder, and this also correctly ignores an unrelated
.kicad_sch that happens to sit in the same directory but isn't actually
part of the sheet tree.

Real (sheet ...) block shape (verified against
test_boards/3CH-AWG-TIA/3CH-AWG-TIA.kicad_sch):
    (sheet
        (at ...) (size ...) ...
        (property "Sheetname" "Channel_1" ...)
        (property "Sheetfile" "channel_tpl.kicad_sch" ...)
        (pin ...) ...
    )
Sheetfile paths are relative to the CURRENT file's own directory (same
resolution rule kicadstamp/config/includes.py uses for include:), not the
root's directory or the current working directory.
"""
import re
from pathlib import Path

from .schematic_blocks import find_balanced_span

_SHEETFILE_RE = re.compile(r'\(property "Sheetfile" "((?:[^"\\]|\\.)*)"')


def _sheet_files_in(text: str) -> list[str]:
    """Sheetfile values (still escaped, see schematic_blocks.
    escape_sexp_string's inverse) referenced by every (sheet ...) block in
    one file's text, in file order."""
    files = []
    for m in re.finditer(r'\(sheet\r?\n', text):
        start = m.start()
        end = find_balanced_span(text, start)
        span_text = text[start:end]
        sf = _SHEETFILE_RE.search(span_text)
        if sf:
            files.append(sf.group(1).replace('\\"', '"').replace('\\\\', '\\'))
    return files


def walk_schematic_hierarchy(root_sheet_path: str) -> list[str]:
    """All .kicad_sch paths reachable from root_sheet_path (itself
    included, first), in discovery order. Cycle/diamond-safe via a `seen`
    set of resolved paths — same shape as
    kicadstamp/config/includes.py's resolve_includes(): a diamond-shared
    sheet (two branches referencing the same file) is visited once, and an
    actual cycle stops instead of recursing forever."""
    root = Path(root_sheet_path).resolve()
    seen: set[Path] = set()
    order: list[str] = []

    def visit(path: Path) -> None:
        if path in seen:
            return
        seen.add(path)
        order.append(str(path))
        text = path.read_text(encoding="utf-8")
        for rel in _sheet_files_in(text):
            visit((path.parent / rel).resolve())

    visit(root)
    return order
