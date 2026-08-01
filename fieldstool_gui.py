#!/usr/bin/env python3
"""
fieldstool_gui.py — GUI for staging Role/Cluster field changes while
KiCad is open (live, read-only selection watch, cross-probed from
Eeschema via the PCB), then applying them to .kicad_sch once KiCad is
closed. See fieldstool/__init__.py and fieldstool/gui/main_window.py for
why staging and applying are two separate phases.

Usage:
    python fieldstool_gui.py [--timeout-ms 20000]
"""
import argparse
import sys
from pathlib import Path

# Project root isn't guaranteed to be importable when this file runs as a
# bare script (python fieldstool_gui.py / python -m fieldstool_gui) from a
# working directory outside the repo, so add it to sys.path in that case.
# When this module is ever imported as part of a package, the package
# machinery already provides the path and a manual insert would be wrong.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from PyQt6.QtWidgets import QApplication

from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.i18n import _
from kicadstamp.logging_setup import setup_logging

from fieldstool.gui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser(description=_("fieldstool GUI"))
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help=_("IPC timeout in ms"))
    parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    app = QApplication(sys.argv)
    window = MainWindow(timeout_ms=args.timeout_ms)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
