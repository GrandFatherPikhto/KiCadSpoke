# fieldstool/exceptions.py
"""
Single exception type for fatal, pre-write problems (bad config, unknown
refdes, an un-expressible conflict, ...) — same "fatal, not silent"
discipline kicadstamp uses (kicadstamp.exceptions.ValidationError), kept
as fieldstool's own type rather than importing kicadstamp's, per the
dependency boundary in fieldstool/__init__.py.
"""


class FieldsToolError(Exception):
    pass
