#!/usr/bin/env python3
"""
recon_symbol_uuid_bridge.py — reconnaissance for the "Pending Changes matches
schematic-vs-board purely by refdes" problem (see the architectural discussion:
gui/schema_model.py load_schematic_components() builds {ref: (role, cluster)}
from .kicad_sch text; gui/docks/pending.py compute_pending_edits() joins that
against the live board snapshot by the refdes STRING only).

Question: does a UUID-based join key exist that uniquely identifies a physical
symbol instance on BOTH sides, without position-in-block hacks?

Empirical findings (3CH-AWG-TIA, 2026-08-08) baked into the docstring because
they answer the design question:

  1. fp.sheet_path.path LAST element is NOT unique per physical footprint.
     On the live board: 232 distinct path[-1] values, 66 shared by 2+ refdes
     (e.g. 0abbbcab... -> C610/C1110/C1610). The last element is the MASTER
     symbol's uuid, shared by every clone of a multi-instance sheet. So a join
     on path[-1] alone is ambiguous whenever clones exist.
  2. fp.sheet_path.path FULL chain IS unique per physical footprint
     (364/364 on the live board, 0 collisions). Full path = sheet-instance
     chain + master-symbol uuid.
  3. The bridge exists and is 100% reliable: board path[-1] is the same uuid
     as the (symbol ...) block's TOP-LEVEL (uuid ...) in .kicad_sch
     (203/203 on the saved .kicad_pcb, 358/364 on the live board).
  4. The schematic's (instances ...) (path ...) chains DO NOT end in the
     symbol uuid — they end in a SHEET uuid (0 of 636 equal the block's
     top-level uuid; only 8 distinct last values on this project, all sheet
     uuids). They also START with the root-sheet uuid, which the board path
     omits. Do NOT use them verbatim as a board join key.
  5. Exact translation that DOES join 1:1:
         board path = (schematic instances path MINUS root uuid) + (block
         top-level uuid)
     e.g. schematic inst path 1e22769e/fb16a0a4/54907477 (R35, dac.kicad_sch)
          -> board path fb16a0a4/54907477/238dea58 (R603 on the live board).
  6. The refdes-based join the current code uses is demonstrably broken on
     real projects: the live board has been re-annotated, and 0/364 live
     refdes match the schematic's refdes for the same symbol uuid. Even the
     saved .kicad_pcb disagrees on 76/279 refdes (board C147 == schematic C105
     == same uuid f03fce3a...). This is exactly the silent-mismatch class of
     bug the refdes join cannot see.

Input:
    project dir (argv[1], default boards/3CH-AWG-TIA). Reads:
      - every *.kicad_sch in that dir (schematic side)
      - <dir>/<basename>.kicad_pcb (board side from the saved file)
      - the live board via kipy (board side from the running KiCad) — optional,
        skipped cleanly if kipy/KiCad are unavailable.

Live KiCad:
    Optional (the saved .kicad_pcb already carries the authoritative (path ...)).

Run:
    python -m kicadstamp.diagnostics.recon_symbol_uuid_bridge [project_dir]
"""
import io
import os
import re
import sys
from collections import Counter, defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from kicadstamp.schematic_blocks import find_balanced_span  # noqa: E402


# ---------------------------------------------------------------------------
# schematic side
# ---------------------------------------------------------------------------
def _path_uuids(path_str: str) -> tuple:
    """'/uuid1/uuid2/uuid3' -> ('uuid1', 'uuid2', 'uuid3'); the leading '/'
    produces no empty element."""
    return tuple(p for p in path_str.split("/") if p)


def parse_schematic_dir(project_dir: str):
    """Schematic-side maps:
      inst_path_refs: list[(path_tuple, refdes, file, top_level_uuid, path_str)]
      top_uuid_refs:  list[(top_level_symbol_uuid, refdes, file)]
      sheet_uuids:    set of (sheet ...) uuids
    """
    inst_path_refs = []
    top_uuid_refs = []
    sheet_uuids = set()
    stats = Counter()

    for fname in sorted(os.listdir(project_dir)):
        if not fname.endswith(".kicad_sch"):
            continue
        path = os.path.join(project_dir, fname)
        try:
            text = open(path, encoding="utf-8").read()
        except Exception as e:
            print(f"  !! cannot read {path}: {e}")
            continue

        for m in re.finditer(r"\(sheet\r?\n", text):
            end = find_balanced_span(text, m.start())
            um = re.search(r'\(uuid "([^"]+)"', text[m.start():end])
            if um:
                sheet_uuids.add(um.group(1))

        for m in re.finditer(r"\(symbol\r?\n", text):
            start = m.start()
            end = find_balanced_span(text, start)
            span = text[start:end]
            # lib_symbols definition has a name immediately after "symbol"
            if re.match(r"\(symbol\s+\"", span):
                continue
            # top-level symbol uuid: first (uuid ...) before any (pin ...)
            pre = span.split("(pin ", 1)[0]
            um = re.search(r'\(uuid "([^"]+)"', pre)
            top_uuid = um.group(1) if um else None
            rm = re.search(r'\(property "Reference" "((?:[^"\\]|\\.)*)"', span)
            ref = rm.group(1) if rm else None
            if top_uuid:
                top_uuid_refs.append((top_uuid, ref, fname))
                stats["blocks_with_top_uuid"] += 1
            else:
                stats["block_no_top_uuid"] += 1

            im = re.search(r"\(instances\r?\n", span)
            if im:
                iend = find_balanced_span(span, im.start())
                inst_text = span[im.start():iend]
                for pm in re.finditer(r'\(path "([^"]+)"\s*\n((?:[^()]|\([^()]*\))*?)\n\s*\)',
                                      inst_text, re.DOTALL):
                    body = pm.group(2)
                    refm = re.search(r'\(reference "((?:[^"\\]|\\.)*)"', body)
                    inst_ref = refm.group(1) if refm else None
                    inst_path_refs.append((_path_uuids(pm.group(1)), inst_ref,
                                           fname, top_uuid, pm.group(1)))
                stats["blocks_with_instances"] += 1

    return {"inst_path_refs": inst_path_refs, "top_uuid_refs": top_uuid_refs,
            "sheet_uuids": sheet_uuids, "stats": stats}


# ---------------------------------------------------------------------------
# board side
# ---------------------------------------------------------------------------
def parse_pcb(pcb_path: str):
    """list[(refdes, footprint_uuid, path_tuple)] from the saved .kicad_pcb."""
    out = []
    text = open(pcb_path, encoding="utf-8").read()
    for m in re.finditer(r'\(footprint\s+"[^"]*"\r?\n', text):
        end = find_balanced_span(text, m.start())
        block = text[m.start():end]
        rm = re.search(r'\(property "Reference" "((?:[^"\\]|\\.)*)"', block)
        pre = block.split("(pad ", 1)[0]
        um = re.search(r'\(uuid "([^"]+)"', pre)
        pm = re.search(r'\(path "([^"]+)"', block)
        if rm and pm:
            out.append((rm.group(1), um.group(1) if um else None,
                        _path_uuids(pm.group(1))))
    return out


def read_live_board():
    """list[(refdes, path_tuple)] or None."""
    try:
        import kipy
    except ImportError:
        return None
    try:
        kc = kipy.KiCad()
        board = kc.get_board()
        return [(fp.reference_field.text.value,
                 tuple(str(u.value) for u in fp.sheet_path.path))
                for fp in board.get_footprints()]
    except Exception as e:
        print(f"  !! live kipy unavailable ({e}) — using saved .kicad_pcb only")
        return None


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------
def _full_key(inst_path: tuple, top_uuid: str) -> tuple:
    """The join key that a board fp.sheet_path.path equals:
    schematic instances path minus its root uuid, plus the block's top uuid.
    For a root-sheet symbol inst_path is (root,) -> key is just (top_uuid,)."""
    return inst_path[1:] + (top_uuid,) if len(inst_path) > 1 else (top_uuid,)


def analyze(project_dir: str):
    print(f"=== Recon: {project_dir} ===\n")

    sch = parse_schematic_dir(project_dir)
    inst_refs = sch["inst_path_refs"]
    top_refs = sch["top_uuid_refs"]
    top_uuids = {t for t, _, _ in top_refs}
    sheet_uuids = sch["sheet_uuids"]

    print(f"[schematic]")
    print(f"  blocks with top-level (uuid ...): {len(top_refs)}")
    print(f"  (path ...) instance entries: {len(inst_refs)}; sheet uuids: {len(sheet_uuids)}")
    eq = sum(1 for _, _, _, top, p in inst_refs
             if top and _path_uuids(p)[-1] == top)
    print(f"  instances path-last == block top-level uuid: {eq} equal, "
          f"{len(inst_refs) - eq} different")
    inst_last = Counter(p[-1] for p, *_ in inst_refs)
    print(f"  distinct instances path-last values: {len(inst_last)} "
          f"({sum(1 for k in inst_last if k in sheet_uuids)} of them sheet uuids) "
          f"-> the (instances ...) path encodes SHEET uuids, not the symbol uuid")

    # ---- board ----
    pcb = parse_pcb(os.path.join(project_dir, os.path.basename(project_dir) + ".kicad_pcb"))
    live = read_live_board()
    board = live if live else [(r, p) for r, _, p in pcb]
    print(f"\n[board]")
    print(f"  saved .kicad_pcb footprints: {len(pcb)}")
    if live:
        print(f"  live board footprints: {len(live)}")
    full_dups = [p for p, c in Counter(p for _, p in board).items() if c > 1]
    last_count = Counter(p[-1] for _, p in board)
    shared_last = {k: c for k, c in last_count.items() if c > 1}
    print(f"  full paths unique per footprint: {len(board) - len(full_dups)}/{len(board)} "
          f"(duplicate full paths: {len(full_dups)})")
    print(f"  distinct path-last values: {len(last_count)}, "
          f"shared by 2+ refdes: {len(shared_last)} "
          f"-> path[-1] is the MASTER symbol uuid, NOT unique per instance")

    # ---- uuid bridge ----
    top_uuid_to_ref = defaultdict(set)
    for t, r, _ in top_refs:
        if r:
            top_uuid_to_ref[t].add(r)
    bridge_pcb = sum(1 for _, _, p in pcb if p[-1] in top_uuids)
    print(f"\n[uuid bridge: board path[-1] == schematic block top-level (uuid ...)]")
    print(f"  saved pcb: {bridge_pcb}/{len(pcb)} footprints' path[-1] is a schematic symbol uuid")
    if live:
        bridge_live = sum(1 for _, p in live if p[-1] in top_uuids)
        print(f"  live:     {bridge_live}/{len(live)}")
    print("  => the board's last path element IS the symbol's own uuid — the bridge is real")

    # ---- full-path join (the reusable key) ----
    key_to_ref = defaultdict(set)
    for tup, ref, fname, top, _ in inst_refs:
        if top:
            key_to_ref[_full_key(tup, top)].add(ref)
    match = sum(1 for _, p in board if p in key_to_ref)
    print(f"\n[full-path join] key = (schematic instances path minus root) + block top uuid")
    print(f"  board full path found in schematic key map: {match}/{len(board)}")
    if live:
        miss_ex = [(r, p) for r, p in live if p not in key_to_ref][:6]
        for r, p in miss_ex:
            print(f"    miss ref={r:8s} path={'/'.join(u[:8] for u in p)}")

    # ---- refdes desync (why the refdes join is dangerous) ----
    agree = sum(1 for r, p in board
                if p[-1] in top_uuid_to_ref and r in top_uuid_to_ref[p[-1]])
    print(f"\n[refdes desync] board refdes == schematic refdes for same symbol uuid: "
          f"{agree}/{len(board)}")
    print("  => on a re-annotated/cloned board most refdes disagree with the schematic, "
          "so a refdes-string join silently pairs the wrong components")

    # ---- per-instance resolution demo ----
    if live:
        top_inst = defaultdict(list)
        for tup, ref, fname, top, _ in inst_refs:
            if top:
                top_inst[top].append((_full_key(tup, top), ref, fname))
        print("\n[per-instance resolution demo] (multi-instance symbols)")
        shown = 0
        for top, entries in top_inst.items():
            if len(entries) > 1 and any(
                    any(p == k for _, p in live) for k, _, _ in entries):
                print(f"  symbol top-uuid {top[:8]}...: schematic instances -> board footprints")
                for key, ref, fname in entries:
                    board_refs = sorted(r for r, p in live if p == key)
                    print(f"    schematic {ref:6s} ({fname:14s}) key={'/'.join(u[:8] for u in key):28s}"
                          f" -> board {board_refs}")
                shown += 1
                if shown >= 2:
                    break


if __name__ == "__main__":
    project_dir = sys.argv[1] if len(sys.argv) > 1 else "boards/3CH-AWG-TIA"
    analyze(project_dir)
