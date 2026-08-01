# fieldstool/gui — the one deliberate exception to fieldstool's "no
# kicadstamp dependency" rule (see fieldstool/__init__.py): connection.py
# reuses kicadstamp.explore.Board for live, READ-ONLY selection-watching
# (staging only — KiCad stays open for this). Nothing here ever calls a
# write method on that connection; Apply always goes through
# fieldstool.editing's offline .kicad_sch splice pipeline instead, which
# needs KiCad closed (see fieldstool/__init__.py for why that can't be
# automated).
