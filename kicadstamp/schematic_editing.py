# kicadstamp/schematic_editing.py
"""
apply_edits() (byte-offset text splicing) plus the shared write pipeline —
dry-run report printing, the running-KiCad guard, and the per-file
.bak -> splice -> sexpdata self-verify -> restore-on-failure sequence.
Ported from tools/apply_role_cluster.py's main() steps 6-8 (2026-08-01
fold-in), factored out of that one script's main() so both the `set` and
`rename` CLI subcommands (fieldstool_cli.py) — and gui/fieldstool_window.
py's Apply button — share one write path instead of three copies of the
same backup/verify logic.
"""
import logging
from dataclasses import dataclass
from pathlib import Path

import sexpdata

from .schematic_safety import list_kicad_pids

logger = logging.getLogger(__name__)

Edit = tuple[int, int, str]  # (start, end, replacement) — see apply_edits


@dataclass
class EditReport:
    """One planned field change, for printing and for driving the actual
    write. kind is 'replace' (property already exists) or 'insert' (new
    property block) — schematic_rename_fields.py never produces 'insert'
    (see its module docstring: renaming only ever touches an
    already-existing value)."""
    file: str
    refs: list[str]
    field: str
    old_value: str | None
    new_value: str
    kind: str


def apply_edits(text: str, edits: list[Edit]) -> str:
    """edits — [(start, end, replacement), ...], need not be sorted;
    several insertions at the same point (start==end) are allowed, their
    relative order is preserved as given."""
    ordered = sorted(range(len(edits)), key=lambda i: (edits[i][0], edits[i][1], i))
    out = []
    cursor = 0
    for idx in ordered:
        start, end, repl = edits[idx]
        if start < cursor:
            raise ValueError("overlapping edits — a bug in the caller, not the config")
        out.append(text[cursor:start])
        out.append(repl)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def print_report(report: list[EditReport], write_mode: bool) -> None:
    print(f"\n=== {'WRITE' if write_mode else 'DRY-RUN'}: {len(report)} edit(s) ===\n")
    for r in sorted(report, key=lambda r: (r.file, r.refs)):
        refs_s = ",".join(r.refs)
        if r.kind == "replace":
            print(f"  [{Path(r.file).name}] {refs_s} .{r.field}: {r.old_value!r} -> {r.new_value!r}")
        else:
            print(f"  [{Path(r.file).name}] {refs_s} .{r.field}: (no field) -> {r.new_value!r}  [new property]")


def check_kicad_not_running(force: bool) -> None:
    """Raises RuntimeError unless no KiCad process is found or the caller
    explicitly overrides — writing around a live Eeschema session risks
    the edit being silently overwritten by KiCad's own next save."""
    pids = list_kicad_pids()
    if pids and not force:
        raise RuntimeError(
            f"KiCad appears to be running (PID {pids}) — the file may be open in Eeschema, "
            f"editing it behind a live session risks the change being silently overwritten by "
            f"KiCad's own next save. Close KiCad and retry, or pass force=True if you're certain "
            f"this file isn't open anywhere."
        )


def write_files(edits_by_file: dict[str, list[Edit]],
                 file_texts: dict[str, str]) -> tuple[list[str], list[str]]:
    """Per file, independently: .bak -> splice -> write -> reparse with
    sexpdata as a self-verify -> on failure, restore the original text and
    record the file as failed, then continue with the rest. Returns
    (written, failed)."""
    written: list[str] = []
    failed: list[str] = []
    for file, edits in edits_by_file.items():
        if not edits:
            continue
        original = file_texts[file]
        bak_path = file + ".bak"
        with open(bak_path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(original)
        new_text = apply_edits(original, edits)
        with open(file, 'w', encoding='utf-8', newline='') as fh:
            fh.write(new_text)
        try:
            with open(file, encoding='utf-8') as fh:
                sexpdata.load(fh)
        except Exception as e:
            logger.error("%s: result does not parse with sexpdata (%s: %s) — restoring from %s",
                         file, type(e).__name__, e, bak_path)
            with open(file, 'w', encoding='utf-8', newline='') as fh:
                fh.write(original)
            failed.append(file)
            continue
        written.append(file)
        logger.info("%s: written, backup at %s, sexpdata self-verify OK", file, bak_path)
    return written, failed
