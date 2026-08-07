#!/usr/bin/env python3
"""
probe_uuid_stability.py — captures footprint ref/UUID snapshots and diffs them,
to check whether a footprint's own UUID (fp.id.value) survives re-annotation.

Input:
    snapshot mode: output JSON path.
    compare mode: two previously captured snapshot JSON files (before, after).

Expected:
    snapshot: writes {ref, id, footprint, sheet_path} for every board footprint
    to JSON, plus capture metadata (timestamp, KiCad version, footprint count).
    compare: loads both snapshots offline (no live KiCad needed) and reports
    UUIDs that appeared/disappeared between them — that is the actual
    instability signal. A refdes changing while the UUID stays the same is
    expected after re-annotation and reported separately, not as a discrepancy.

Live KiCad:
    snapshot needs a running KiCad with the board open; compare is offline.

Run:
    python -m kicadstamp.diagnostics.probe_uuid_stability snapshot uuid_before.json
    ... re-annotate in Eeschema, then Update PCB from Schematic
        (Match Method = "Re-associate by UUID/timestamp") ...
    python -m kicadstamp.diagnostics.probe_uuid_stability snapshot uuid_after.json
    python -m kicadstamp.diagnostics.probe_uuid_stability compare uuid_before.json uuid_after.json
"""
import argparse
import io
import json
import sys
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


def _capture_snapshot():
    import kipy

    kc = kipy.KiCad()
    board = kc.get_board()
    footprints = list(board.get_footprints())

    entries = []
    for fp in footprints:
        sp = fp.sheet_path
        entries.append({
            "ref": fp.reference_field.text.value,
            "id": fp.id.value,
            "footprint": str(fp.definition.id),
            "sheet_path": sp.path_human_readable if hasattr(sp, "path_human_readable") else None,
        })

    try:
        kicad_version = str(kc.get_version())
    except Exception:
        kicad_version = None

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "kicad_version": kicad_version,
        "count": len(entries),
        "footprints": entries,
    }


def cmd_snapshot(args):
    data = _capture_snapshot()
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"Снято {data['count']} футпринтов -> {args.output}")
    return 0


def _load_snapshot(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_compare(args):
    before = _load_snapshot(args.before)
    after = _load_snapshot(args.after)

    by_id_before = {e["id"]: e for e in before["footprints"]}
    by_id_after = {e["id"]: e for e in after["footprints"]}

    ids_before = set(by_id_before)
    ids_after = set(by_id_after)

    added = ids_after - ids_before
    removed = ids_before - ids_after
    common = ids_before & ids_after

    ref_changed = [
        (uid, by_id_before[uid]["ref"], by_id_after[uid]["ref"])
        for uid in sorted(common)
        if by_id_before[uid]["ref"] != by_id_after[uid]["ref"]
    ]

    print(f"До ({args.before}): {len(ids_before)} футпринтов")
    print(f"После ({args.after}): {len(ids_after)} футпринтов")
    print(f"Общих UUID: {len(common)}")
    print(f"Появившихся UUID: {len(added)}")
    print(f"Пропавших UUID: {len(removed)}")
    print(f"Refdes поменялся, UUID тот же (ожидаемо при реаннотации): {len(ref_changed)}")

    if added:
        print("\n=== РАСХОЖДЕНИЕ: UUID есть в 'after', не было в 'before' ===")
        for uid in sorted(added):
            e = by_id_after[uid]
            print(f"  {uid}  ref={e['ref']}  footprint={e['footprint']}  sheet={e['sheet_path']}")

    if removed:
        print("\n=== РАСХОЖДЕНИЕ: UUID был в 'before', пропал в 'after' ===")
        for uid in sorted(removed):
            e = by_id_before[uid]
            print(f"  {uid}  ref={e['ref']}  footprint={e['footprint']}  sheet={e['sheet_path']}")

    if args.verbose and ref_changed:
        print("\n=== Refdes переехал (не расхождение, тот же UUID) ===")
        for uid, r_before, r_after in ref_changed:
            print(f"  {uid}  {r_before} -> {r_after}")

    if added or removed:
        print("\nUUID НЕ стабильны: набор UUID изменился между снимками.")
        return 1

    print("\nUUID стабильны: набор идентичен, менялись только refdes.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="capture current board footprint UUIDs to JSON")
    p_snap.add_argument("output", help="output JSON path")
    p_snap.set_defaults(func=cmd_snapshot)

    p_cmp = sub.add_parser("compare", help="diff two snapshots, report UUID discrepancies")
    p_cmp.add_argument("before", help="snapshot JSON captured before re-annotation")
    p_cmp.add_argument("after", help="snapshot JSON captured after re-annotation")
    p_cmp.add_argument("-v", "--verbose", action="store_true",
                        help="also list refdes remaps for UUIDs that stayed the same")
    p_cmp.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
