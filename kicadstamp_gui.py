#!.venv/bin/python
"""
kicadstamp_gui.py — persistent PyQt6 GUI for browsing/tagging the live
board over kipy IPC, alongside kicadstamp_cli.py for scripted batch work.

Step 1 (see gui/main_window.py): connection lifecycle + a Role/Cluster tree
dock, click a component/group to highlight it on the real board. Meant to
be left open while working in KiCad, not run once and closed like the CLI.

Usage:
    python kicadstamp_gui.py [--timeout-ms 20000] [--verbose]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# See kicadstamp_cli.py for why this is needed (UnicodeEncodeError on legacy
# console codepages with translated/typographic text).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from PyQt6.QtWidgets import QApplication

from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.i18n import _
from kicadstamp.logging_setup import setup_logging

from gui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser(description=_("KiCadStamp GUI"))
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS,
                        help=_("IPC timeout in ms"))
    parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    app = QApplication(sys.argv)
    window = MainWindow(timeout_ms=args.timeout_ms)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
