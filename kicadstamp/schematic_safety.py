# kicadstamp/schematic_safety.py
"""
Pre-write safety guards for the schematic-editing tools (schematic_
set_fields.py/schematic_rename_fields.py, fieldstool_cli.py) — ported from
tools/apply_role_cluster.py (2026-08-01 fold-in). Kept dependency-free from
kipy on purpose (this tool must work with KiCad closed) — psutil is
optional, used only on non-Windows.
"""
import logging
import os
import subprocess
import unicodedata

logger = logging.getLogger(__name__)


def find_non_ascii(value: str) -> list[tuple[int, str, int, str]]:
    bad = []
    for i, ch in enumerate(value):
        if not (0x20 <= ord(ch) <= 0x7E):
            try:
                name = unicodedata.name(ch)
            except ValueError:
                name = "UNNAMED"
            bad.append((i, ch, ord(ch), name))
    return bad


def list_kicad_pids() -> list[int]:
    """If KiCad is running, a .kicad_sch we're about to splice may be open
    in Eeschema, and an edit made around that live session risks being
    silently overwritten by KiCad's own next save."""
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
            import psutil  # optional, see requirements.txt
            pids = [p.pid for p in psutil.process_iter(["name"])
                    if p.info["name"] and "kicad" in p.info["name"].lower()]
    except Exception as e:
        logger.debug("could not get kicad PIDs: %s", e)
    return sorted(pids)
