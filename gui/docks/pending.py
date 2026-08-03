# gui/docks/pending.py
"""
Pending changes — 2026-08-03 redesign. Used to be a JSON-backed staging
queue (PendingRegistry) you built up by hand while KiCad was open, applied
later. Retired: it could drift out of sync with the live board on its own
(found live — Clear all/Delete selected wrote Role/Cluster straight to the
board over IPC but never staged anything, so Apply had nothing to do and
stayed disabled even though the board had genuinely changed).

New model: whatever is currently on the live board (via IPC — Clear all,
Delete selected, fieldstool's own Stage button, PlacerDock's Cluster
tagging, ANY of them) already IS the accumulated pending state — there is
nothing left to separately track. compute_pending_edits() below just diffs
that live state against the schematic's last-known Role/Cluster and returns
whatever differs; Apply writes exactly that diff into the schematic via
kicadstamp.schematic_set_fields.plan_set_edits_for_root() (unchanged). This
can never drift, because it is never stored — recomputed fresh from two
already-cached sources every time (gui.schema_model.SchematicComponent list,
refreshed by an explicit Rescan; BoardConnection.snapshot, refreshed by the
main GUI's ~2s poll), so it costs no new IPC/file reads.

Deliberately no persistence and no undo/remove-from-pending action: if the
live board's Role/Cluster genuinely differs from the schematic, that is
simply a fact about the board right now, not a staged decision to revoke —
to not apply a change, revert the field's value on the board itself (Ctrl+Z
in KiCad works immediately after an edit).
"""
import logging
from dataclasses import dataclass
from typing import Dict, List

logger = logging.getLogger(__name__)


@dataclass
class PendingEdit:
    ref: str
    field: str
    old_value: str  # currently in the schematic
    new_value: str  # currently on the live board


def compute_pending_edits(components, snapshot) -> List[PendingEdit]:
    """components: List[gui.schema_model.SchematicComponent] (from
    load_schematic_components(), i.e. the schematic's last Rescan).
    snapshot: List[kicadstamp.explore.Selected] (BoardConnection.snapshot,
    i.e. the board's last poll tick). Only refs present in BOTH are
    comparable — a ref only on the board (not yet in this schematic tree at
    all) or only in the schematic (not currently on the board) has nothing
    to diff. A component whose OWN blocks disagree on Role/Cluster
    (SchematicComponent.divergent — a pre-existing schema inconsistency,
    not caused by this diff) is compared against its first block's value
    like everywhere else that reads .role/.cluster; if that differs from
    the board, Apply's own plan_set_edits_for_root() will simply unify all
    of that ref's blocks to the board's value, which is a reasonable
    resolution, not a bug."""
    by_ref = {c.ref: c for c in components}
    edits: List[PendingEdit] = []
    for s in snapshot:
        c = by_ref.get(s.ref)
        if c is None:
            continue
        if (s.role or "") != (c.role or ""):
            edits.append(PendingEdit(s.ref, "Role", c.role or "", s.role or ""))
        if (s.cluster or "") != (c.cluster or ""):
            edits.append(PendingEdit(s.ref, "Cluster", c.cluster or "", s.cluster or ""))
    return sorted(edits, key=lambda e: (e.ref, e.field))


def edits_to_fields_cfg(edits: List[PendingEdit]) -> Dict[str, Dict[str, str]]:
    """refdes -> {field: value} — the shape
    kicadstamp.schematic_set_fields.plan_set_edits_for_root() consumes."""
    cfg: Dict[str, Dict[str, str]] = {}
    for e in edits:
        cfg.setdefault(e.ref, {})[e.field] = e.new_value
    return cfg


try:
    from PyQt6.QtWidgets import (QAbstractItemView, QDockWidget, QHBoxLayout,
                                 QPushButton, QTableWidget, QTableWidgetItem,
                                 QVBoxLayout, QWidget)

    from kicadstamp.i18n import _
except ImportError:  # pragma: no cover — the functions above are usable without PyQt6
    QDockWidget = object


class PendingChangesDock(QDockWidget):
    """Read-only table of the current schematic-vs-board diff (see
    compute_pending_edits above) + an Apply button MainWindow wires (see
    gui/fieldstool_window.py) — Apply itself needs the root_sheet path and
    the KiCad-running guard, which this dock deliberately doesn't know
    about. Fed wholesale by set_edits() every time either side of the diff
    changes (Rescan, or the main GUI's ~2s poll) — never mutated in place,
    same "just show me the latest" discipline as the rest of this GUI."""

    def __init__(self, main_window):
        super().__init__(_("Pending changes"), main_window)
        self._main_window = main_window
        self.on_apply_clicked = None  # Callable[[], None], set by MainWindow

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [_("Ref"), _("Field"), _("Schematic (current)"), _("Board (new)")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        self.apply_button = QPushButton(_("Apply..."))
        self.apply_button.clicked.connect(lambda: self.on_apply_clicked and self.on_apply_clicked())
        button_row.addWidget(self.apply_button)
        layout.addLayout(button_row)

        self.setWidget(container)
        self.set_edits([])

    def set_edits(self, edits: List[PendingEdit]) -> None:
        self.table.setRowCount(len(edits))
        for row, e in enumerate(edits):
            self.table.setItem(row, 0, QTableWidgetItem(e.ref))
            self.table.setItem(row, 1, QTableWidgetItem(e.field))
            self.table.setItem(row, 2, QTableWidgetItem(e.old_value))
            self.table.setItem(row, 3, QTableWidgetItem(e.new_value))
        self.apply_button.setEnabled(bool(edits))
