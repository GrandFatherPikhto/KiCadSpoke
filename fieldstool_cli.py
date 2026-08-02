#!/usr/bin/env python3
"""
fieldstool_cli.py — bulk-edit Role/Cluster (or any custom field) directly
in .kicad_sch, offline (no kipy, no live KiCad connection at all). Two
subcommands:

  set     refdes -> {field: value}, from YAML (ports tools/apply_role_
          cluster.py's original behavior — that script is retired, see
          docs/commands_ru.md).
  rename  field -> {old_value: new_value}, applied to every symbol whose
          CURRENT value matches, project-wide — no refdes enumeration.

Both: dry-run by default, --write to actually touch files, --allow-non-
ascii to skip the homoglyph-typo guard, --force-with-kicad-running to
skip the running-KiCad guard. See docs/fieldstool.md for why this stays a
separate CLI/interface from kicadstamp_cli.py (this writes .kicad_sch
directly, a fundamentally different risk class from kicadstamp's
live-IPC-only writes) even though the underlying kicadstamp.schematic_*
modules now live in the same package.

Usage:
    python fieldstool_cli.py set roles.yaml                # dry-run
    python fieldstool_cli.py set roles.yaml --write
    python fieldstool_cli.py rename renames.yaml --write
"""
import argparse
import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# fieldstool_cli.py runs as a bare script (no package context), and the
# project root — where kicadstamp/ lives — is that script's own directory,
# already on sys.path by default; no sys.path.insert needed (unlike
# kicadstamp_cli.py/kicadstamp_gui.py, see their own comments on this).
from kicadstamp.exceptions import FieldsToolError
from kicadstamp.schematic_editing import check_kicad_not_running, print_report, write_files
from kicadstamp.schematic_rename_fields import plan_rename_edits
from kicadstamp.schematic_safety import find_non_ascii
from kicadstamp.schematic_set_fields import plan_set_edits

logger = logging.getLogger(__name__)


def _check_ascii(report, *, allow_non_ascii: bool) -> None:
    if allow_non_ascii:
        return
    bad = [r for r in report if find_non_ascii(r.new_value)]
    if not bad:
        return
    lines = [f"[FATAL] {len(bad)} new value(s) contain non-ASCII characters — exactly the "
             f"homoglyph-typo class this tool guards against by default. Nothing written.\n"]
    for r in bad:
        lines.append(f"  {','.join(r.refs)}.{r.field} = {r.new_value!r}")
        for i, ch, cp, name in find_non_ascii(r.new_value):
            lines.append(f"      position {i}: {ch!r} U+{cp:04X} ({name})")
    lines.append("\nIf non-ASCII is intentional here, rerun with --allow-non-ascii.")
    sys.exit("\n".join(lines))


def _run(report, edits_by_file, file_texts, *, write: bool, force_with_kicad_running: bool) -> int:
    if not report:
        print("Nothing to change — every requested value already matches.")
        return 0

    print_report(report, write_mode=write)
    if not write:
        print("\nDry-run — nothing written. Rerun with --write to apply.")
        return 0

    try:
        check_kicad_not_running(force=force_with_kicad_running)
    except RuntimeError as e:
        sys.exit(f"[error] {e}")

    written, failed = write_files(edits_by_file, file_texts)
    print(f"\nFiles written: {len(written)}. Backups alongside them, .bak extension.")
    if failed:
        print(f"FAILED to write (restored from .bak): {failed}")
        return 1
    return 0


def cmd_set(args) -> int:
    try:
        edits_by_file, file_texts, report = plan_set_edits(Path(args.config))
    except FieldsToolError as e:
        sys.exit(f"[error] {e}")
    _check_ascii(report, allow_non_ascii=args.allow_non_ascii)
    return _run(report, edits_by_file, file_texts,
                write=args.write, force_with_kicad_running=args.force_with_kicad_running)


def cmd_rename(args) -> int:
    try:
        edits_by_file, file_texts, report, unmatched = plan_rename_edits(Path(args.config))
    except FieldsToolError as e:
        sys.exit(f"[error] {e}")
    _check_ascii(report, allow_non_ascii=args.allow_non_ascii)
    if unmatched:
        print("[warning] these old values matched nothing anywhere (already renamed, or a typo):")
        for u in unmatched:
            print(f"  {u}")
    return _run(report, edits_by_file, file_texts,
                write=args.write, force_with_kicad_running=args.force_with_kicad_running)


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("config", help="YAML config (see fieldstool_cli.py --help for shape)")
    p.add_argument("--write", action="store_true", help="actually write (otherwise dry-run only)")
    p.add_argument("--allow-non-ascii", action="store_true",
                    help="do not check new values for non-ASCII characters (not recommended)")
    p.add_argument("--force-with-kicad-running", action="store_true",
                    help="write even if a running KiCad process is detected")
    p.add_argument("--verbose", action="store_true")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bulk-edit Role/Cluster (or any custom field) directly in .kicad_sch.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser(
        "set", help="assign field values to a list of refdes (config: root_sheet + fields:)")
    _add_common_flags(set_parser)
    set_parser.set_defaults(func=cmd_set)

    rename_parser = subparsers.add_parser(
        "rename", help="rename a field's value everywhere it occurs (config: root_sheet + renames:)")
    _add_common_flags(rename_parser)
    rename_parser.set_defaults(func=cmd_rename)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
