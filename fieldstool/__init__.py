# fieldstool — standalone tool for bulk set/rename of Role/Cluster (or any
# custom field) directly in .kicad_sch — not a PCB-side IPC field write
# (kicadstamp/gui used to have exactly that as BulkFieldEditorDock; retired
# 2026-08-01 in favor of embedding fieldstool itself, see gui/docks/
# fieldstool_dock.py and docs/gui.md).
#
# fieldstool/, fieldstool/gui/, kicadstamp/, and gui/ stay separate PACKAGES
# (decided 2026-08-01, see techdocs/handoff/handoff_2026_08_01_fieldstool.md)
# — this is about the write pipeline, not process/window boundaries:
# - kicadstamp/gui only ever writes through KiCad's live IPC/commit path
#   (PCB Editor open, transactional, undo-able). This tool instead edits
#   .kicad_sch as text directly — a genuinely different, riskier write
#   surface (same hazard class as KiCad bug #24966: touching a file KiCad
#   may have open/cached — but worse, since it bypasses KiCad's own live
#   IPC entirely). Only fieldstool.gui.connection is a deliberate
#   exception, and even that is read-only.
# - Apply (the actual .kicad_sch write) needs KiCad CLOSED — confirmed
#   kipy 0.7.1 exposes no app-level quit/save-all/unsaved-changes-check
#   call at all, so this can only ever be an instruction, never automated.
#   This is a per-window requirement, independent of whether fieldstool's
#   own MainWindow is run standalone (fieldstool_gui.py) or embedded as a
#   dock inside kicadstamp_gui.py (gui/docks/fieldstool_dock.py, added
#   2026-08-01) — either way, Apply still just tells the user to close
#   KiCad first.
#
# fieldstool/blocks.py, discovery.py, safety.py, editing.py, set_fields.py,
# rename_fields.py, and fieldstool_cli.py are dependency-free from
# kicadstamp — no kipy import anywhere in that offline core. fieldstool/gui/
# itself stays dependency-free of gui/ too — gui/docks/fieldstool_dock.py is
# the one place that imports fieldstool.gui.main_window, not the other way
# around.
#
# Status 2026-08-01: offline core (set/rename via fieldstool_cli.py),
# fieldstool/gui/ (live-selection staging + Apply, fieldstool_gui.py), and
# embedding as a kicadstamp_gui.py dock all done — see
# techdocs/handoff/handoff_2026_08_01_fieldstool.md. Not yet verified
# against a live KiCad session (offscreen/synthetic tests only).
