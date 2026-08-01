# fieldstool — standalone tool for bulk set/rename of Role/Cluster (or any
# custom field) directly in .kicad_sch — not the PCB-side IPC field write
# kicadstamp/gui's BulkFieldEditorDock already does.
#
# Deliberately separate from kicadstamp/ and gui/ (decided 2026-08-01, see
# techdocs/handoff/handoff_2026_08_01_fieldstool.md):
# - kicadstamp/gui only ever writes through KiCad's live IPC/commit path
#   (PCB Editor open, transactional, undo-able). This tool instead edits
#   .kicad_sch as text directly — a genuinely different, riskier write
#   surface (same hazard class as KiCad bug #24966: touching a file KiCad
#   may have open/cached — but worse, since it bypasses KiCad's own live
#   IPC entirely). Only fieldstool.gui.connection (once it exists) is a
#   deliberate exception, and even that is read-only.
# - Apply (the actual .kicad_sch write) needs KiCad CLOSED — confirmed
#   kipy 0.7.1 exposes no app-level quit/save-all/unsaved-changes-check
#   call at all, so this can only ever be an instruction, never automated.
#   That requirement is incompatible with kicadstamp/gui's always-open-
#   alongside-KiCad model, so the two stay separate processes/UIs.
#
# fieldstool/blocks.py, discovery.py, safety.py, editing.py, set_fields.py,
# rename_fields.py, and fieldstool_cli.py are dependency-free from
# kicadstamp — no kipy import anywhere in that offline core.
#
# Status 2026-08-01: offline core (set/rename via fieldstool_cli.py) done.
# fieldstool/gui/ (live-selection staging + Apply) not yet built.
