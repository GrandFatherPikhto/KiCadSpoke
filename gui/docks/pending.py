# gui/docks/pending.py
"""
PendingRegistry — JSON-backed queue of staged field assignments (ref,
field, new_value), built up while KiCad is open (staging never touches
.kicad_sch), applied all at once later while KiCad is closed (see
docs/fieldstool.md). Renaming a whole tree group just stages one entry
per member ref — by the time anything reaches Apply it's already plain
refdes -> {field: value}, the same shape kicadstamp.schematic_set_fields
always used (see its plan_set_edits_for_root()).

PendingRegistry itself has no Qt/kipy dependency (testable without a
QApplication) — PendingChangesDock below is the thin Qt wrapper around it.
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class PendingEdit:
    ref: str
    field: str
    new_value: str


class PendingRegistry:
    """Keyed by (ref, field) — staging the same (ref, field) again
    overwrites the earlier pending value rather than duplicating it (the
    last thing you picked in the GUI is what Apply should use)."""

    def __init__(self, path: Path):
        self.path = path
        self._entries: Dict[Tuple[str, str], str] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._entries = {(e["ref"], e["field"]): e["new_value"] for e in raw}
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to read %s, starting empty: %s", self.path, e)
            self._entries = {}

    def save(self) -> None:
        raw = [{"ref": ref, "field": field, "new_value": value}
               for (ref, field), value in sorted(self._entries.items())]
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.warning("Failed to write %s: %s", self.path, e)

    def stage(self, ref: str, field: str, new_value: str) -> None:
        self._entries[(ref, field)] = new_value
        self.save()

    def remove(self, ref: str, field: str) -> None:
        self._entries.pop((ref, field), None)
        self.save()

    def clear(self) -> None:
        self._entries.clear()
        self.save()

    def entries(self) -> List[PendingEdit]:
        return [PendingEdit(ref, field, value)
                for (ref, field), value in sorted(self._entries.items())]

    def as_fields_cfg(self) -> Dict[str, Dict[str, str]]:
        """refdes -> {field: value} — the shape
        kicadstamp.schematic_set_fields.plan_set_edits_for_root() consumes."""
        cfg: Dict[str, Dict[str, str]] = {}
        for (ref, field), value in self._entries.items():
            cfg.setdefault(ref, {})[field] = value
        return cfg


try:
    from PyQt6.QtWidgets import (QAbstractItemView, QDockWidget, QHBoxLayout,
                                 QPushButton, QTableWidget, QTableWidgetItem,
                                 QVBoxLayout, QWidget)

    from kicadstamp.i18n import _
except ImportError:  # pragma: no cover — PendingRegistry above is usable without PyQt6
    QDockWidget = object


class PendingChangesDock(QDockWidget):
    """Table of staged edits, remove-selected/clear-all, and an Apply
    button MainWindow wires (see gui/fieldstool_window.py) — Apply itself
    needs the root_sheet path and the KiCad-running guard, which this dock
    deliberately doesn't know about."""

    def __init__(self, main_window, registry: PendingRegistry):
        super().__init__(_("Pending changes"), main_window)
        self._main_window = main_window
        self.registry = registry
        self.on_apply_clicked = None  # Callable[[], None], set by MainWindow

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([_("Ref"), _("Field"), _("New value")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        self.remove_button = QPushButton(_("Remove selected"))
        self.remove_button.clicked.connect(self._on_remove_selected)
        button_row.addWidget(self.remove_button)
        self.clear_button = QPushButton(_("Clear all"))
        self.clear_button.clicked.connect(self._on_clear)
        button_row.addWidget(self.clear_button)
        self.apply_button = QPushButton(_("Apply..."))
        self.apply_button.clicked.connect(lambda: self.on_apply_clicked and self.on_apply_clicked())
        button_row.addWidget(self.apply_button)
        layout.addLayout(button_row)

        self.setWidget(container)
        self.refresh()

    def stage(self, ref: str, field: str, new_value: str) -> None:
        self.registry.stage(ref, field, new_value)
        self.refresh()

    def stage_group(self, refs: List[str], field: str, new_value: str) -> None:
        """Group-rename entry point: one pending entry per member ref, see
        module docstring — this is the mechanism a tree group-rename
        action (gui.fieldstool_window.MainWindow._on_group_picked) feeds
        into."""
        for ref in refs:
            self.registry.stage(ref, field, new_value)
        self.refresh()

    def refresh(self) -> None:
        entries = self.registry.entries()
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            self.table.setItem(row, 0, QTableWidgetItem(e.ref))
            self.table.setItem(row, 1, QTableWidgetItem(e.field))
            self.table.setItem(row, 2, QTableWidgetItem(e.new_value))
        self.apply_button.setEnabled(bool(entries))

    def _on_remove_selected(self) -> None:
        for row in sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True):
            ref = self.table.item(row, 0).text()
            field = self.table.item(row, 1).text()
            self.registry.remove(ref, field)
        self.refresh()

    def _on_clear(self) -> None:
        self.registry.clear()
        self.refresh()
