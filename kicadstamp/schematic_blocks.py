# kicadstamp/schematic_blocks.py
"""
Locating (symbol ...) instance blocks and (property "Field" "value" ...)
spans inside .kicad_sch text — ported from tools/apply_role_cluster.py
(2026-08-01 fold-in), later the fieldstool package (2026-08-02: folded
into kicadstamp/ proper, see docs/fieldstool.md), logic unchanged.

Point-edit, not parse->dump: byte-offset spans for splicing, not a full
sexpdata parse/serialize round trip — there is no precedent that
sexpdata.dumps() reproduces KiCad's own formatting byte-for-byte (see
schematic_editing.py for the splice application and self-verify step).
"""
import re


def find_balanced_span(text: str, open_idx: int) -> int:
    """text[open_idx] must be '(' — returns the index right AFTER the
    matching ')', accounting for quoted strings (KiCad escapes a quote
    inside a string as \\")."""
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
    raise ValueError(f"unbalanced parentheses starting at position {open_idx}")


class SymbolBlock:
    __slots__ = ("file", "start", "end", "refs", "uuid", "instances")

    def __init__(self, file, start, end, refs, uuid=None, instances=()):
        self.file = file
        self.start = start
        self.end = end
        self.refs = refs  # set of refdes from (instances ...)
        self.uuid = uuid  # top-level (uuid ...) of the block — the symbol's own id
        self.instances = instances  # tuple[(path_uuids_tuple, refdes)] — one per
                                    # (instances ...) (path ...), the per-instance chain

    @property
    def id(self):
        return (self.file, self.start)


def _path_uuids(path_str: str) -> tuple:
    """'/uuid1/uuid2/uuid3' -> ('uuid1', 'uuid2', 'uuid3'); the leading '/'
    produces no empty element."""
    return tuple(p for p in path_str.split("/") if p)


def iter_symbol_blocks(path: str, text: str) -> list[SymbolBlock]:
    """All PLACED symbol instance blocks — NOT lib_symbols definitions.
    Verified against real files, no exceptions found: a lib_symbols
    definition is always `(symbol "Lib:Name" ...)` (a name immediately
    after "symbol"), a placed instance has a newline immediately after
    "symbol" instead, no name there at all.

    Also extracts:
    - the block's TOP-LEVEL (uuid ...) — the symbol's own identity, the same
      uuid that appears as fp.sheet_path.path[-1] on the board (see
      kicadstamp/diagnostics/recon_symbol_uuid_bridge.py). It is the FIRST
      (uuid ...) before any (pin ...) — every (pin ...) has its own
      (uuid ...), which must not be mistaken for the symbol's. None if the
      block has no such uuid (older/foreign files), never "".
    - the per-instance (path ...) chains from (instances ...) as (uuid_tuple,
      refdes) — the raw schematic-side instance paths (they start with the
      root uuid and end in a SHEET uuid, unlike the board's sheet_path.path;
      the board-facing key is built in gui/schema_model.load_schematic_instances).
    """
    blocks = []
    for m in re.finditer(r'\(symbol\r?\n', text):
        start = m.start()
        end = find_balanced_span(text, start)
        span_text = text[start:end]
        pre = span_text.split("(pin ", 1)[0]
        um = re.search(r'\(uuid "((?:[^"\\]|\\.)*)"', pre)
        symbol_uuid = um.group(1) if um else None
        instances_m = re.search(r'\(instances\r?\n', span_text)
        refs = set()
        instances = []
        if instances_m:
            inst_start = instances_m.start()
            inst_end = find_balanced_span(span_text, inst_start)
            inst_text = span_text[inst_start:inst_end]
            for ref_m in re.finditer(r'\(reference "((?:[^"\\]|\\.)*)"', inst_text):
                refs.add(ref_m.group(1))
            for pm in re.finditer(r'\(path "([^"]+)"\s*\n((?:[^()]|\([^()]*\))*?)\n\s*\)',
                                  inst_text, re.DOTALL):
                refm = re.search(r'\(reference "((?:[^"\\]|\\.)*)"', pm.group(2))
                if refm:
                    instances.append((_path_uuids(pm.group(1)), refm.group(1)))
        blocks.append(SymbolBlock(path, start, end, refs, symbol_uuid, tuple(instances)))
    return blocks


def find_property_value_span(span_text: str, field: str) -> tuple[int, int] | None:
    """(start, end) of the value (inside the quotes, relative to
    span_text) for (property "{field}" "..." ...), or None if this block
    has no such property."""
    m = re.search(r'\(property "' + re.escape(field) + r'" "((?:[^"\\]|\\.)*)"', span_text)
    if not m:
        return None
    return m.start(1), m.end(1)


def find_symbol_at(span_text: str) -> tuple[str, str]:
    """(x, y) from the symbol's own (at X Y ROT) — the first (at ...) in
    the block, before any (property ...); hidden bookkeeping fields like
    Role/Cluster always use the symbol's own coordinates in real files,
    not some separate offset."""
    m = re.search(r'\(at (-?[0-9.]+) (-?[0-9.]+)', span_text)
    if not m:
        raise ValueError("no (at ...) found on the symbol")
    return m.group(1), m.group(2)


def find_insertion_point(span_text: str) -> tuple[int, str]:
    """(index in span_text, indent) to insert a new (property ...) — before
    the first (pin ...), or before (instances ...) if there are no pins at
    all (safety net, never seen in real files)."""
    m = re.search(r'\n([ \t]*)\(pin ', span_text)
    if not m:
        m = re.search(r'\n([ \t]*)\(instances\r?\n', span_text)
    if not m:
        raise ValueError("no insertion point found ((pin ...) and (instances ...) both absent)")
    return m.start() + 1, m.group(1)


def escape_sexp_string(value: str) -> str:
    return value.replace('\\', '\\\\').replace('"', '\\"')


def unescape_sexp_string(value: str) -> str:
    """Inverse of escape_sexp_string(): first collapses an escaped quote
    (backslash + double-quote) back to a plain double-quote, then collapses
    an escaped backslash (backslash + backslash) back to one backslash.
    Read a (property ...) value out of .kicad_sch text with this before
    comparing it to a raw config value."""
    return value.replace('\\"', '"').replace('\\\\', '\\')


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
