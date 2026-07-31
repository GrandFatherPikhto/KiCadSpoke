# gui/docks/bulk_field_editor.py
"""
BulkFieldEditorDock — set Role and/or Cluster on every currently-selected
component in one write. Step 2 of the planned GUI (see gui/main_window.py's
docstring for step 1): the first real mutating panel, so unlike the
Role/Cluster tree it goes through KiCadBoardAdapter.set_field_values_bulk()
(one undo-able transaction for the whole batch) rather than anything
read-only.

Target selection is NOT picked in this dock itself — it always reflects
whatever's currently selected on the board (mouse in KiCad, or a tree-node
click), fed by MainWindow's existing selection-watch timer (see
set_board_selection()). No separate "pick components" step to keep in sync
with the board.
"""
import logging
from typing import List, Optional

from PyQt6.QtWidgets import (QComboBox, QDockWidget, QFormLayout, QLabel,
                              QPushButton, QVBoxLayout, QWidget)

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.exceptions import ValidationError
from kicadstamp.explore import Board, Selected
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

_ERROR_STYLE = "color: #a00;"
_SUCCESS_STYLE = "color: #070;"


class BulkFieldEditorDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__(_("Bulk edit"), main_window)
        self._main_window = main_window
        self._selected: List[Selected] = []

        container = QWidget()
        layout = QVBoxLayout(container)

        self.selection_label = QLabel(_("Nothing selected"))
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)

        form = QFormLayout()
        self.role_combo = QComboBox()
        self.role_combo.setEditable(True)
        form.addRow(_("Role:"), self.role_combo)

        self.cluster_combo = QComboBox()
        self.cluster_combo.setEditable(True)
        form.addRow(_("Cluster:"), self.cluster_combo)
        layout.addLayout(form)

        self.apply_button = QPushButton(_("Apply to selection"))
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._on_apply)
        layout.addWidget(self.apply_button)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        layout.addStretch(1)
        self.setWidget(container)

    def set_board_selection(self, selected: List[Selected]) -> None:
        """Called by MainWindow every selection-watch tick (same feed the
        tree's highlight_board_selection() gets) — keeps the edit target in
        sync with whatever's actually selected in KiCad right now."""
        self._selected = selected
        if not selected:
            self.selection_label.setText(_("Nothing selected"))
        else:
            refs = ", ".join(sorted(s.ref for s in selected))
            self.selection_label.setText(
                _("{count} selected: {refs}").format(count=len(selected), refs=refs))
        self.apply_button.setEnabled(bool(selected))

    def refresh_known_values(self, board: Board) -> None:
        """Populates the Role/Cluster combo boxes with distinct values
        already used on the board — autocomplete, so a typo doesn't create
        an orphan Cluster branch nobody notices until much later. Called by
        MainWindow alongside a full snapshot refresh, not every tick (would
        fight the user's in-progress typing in an editable combo for no
        benefit — the known-value list barely changes tick to tick)."""
        snapshot = board.select()
        roles = sorted({s.role for s in snapshot if s.role})
        clusters = sorted({s.cluster for s in snapshot if s.cluster})
        self._set_combo_items(self.role_combo, roles)
        self._set_combo_items(self.cluster_combo, clusters)

    @staticmethod
    def _set_combo_items(combo: QComboBox, items: List[str]) -> None:
        current_text = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        combo.setCurrentText(current_text)
        combo.blockSignals(False)

    def _on_apply(self) -> None:
        self.message_label.setStyleSheet("")
        self.message_label.setText("")
        new_role = self.role_combo.currentText().strip() or None
        new_cluster = self.cluster_combo.currentText().strip() or None
        if new_role is None and new_cluster is None:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(_("Nothing to apply — set Role and/or Cluster first."))
            return
        if not self._selected:
            return

        board = self._main_window.connection.board
        if board is None:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(_("Not connected."))
            return

        conflicts = self._find_conflicts(board, new_role, new_cluster)
        if conflicts:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(
                _("Blocked — would duplicate a Role within a Cluster:\n") + "\n".join(conflicts))
            return

        updates = []
        for s in self._selected:
            if new_role is not None:
                updates.append((s.fp, ROLE_FIELD_NAME, new_role))
            if new_cluster is not None:
                updates.append((s.fp, CLUSTER_FIELD_NAME, new_cluster))

        fields_changed = "/".join(f for f in
                                   [_("Role") if new_role is not None else None,
                                    _("Cluster") if new_cluster is not None else None] if f)
        description = _("Bulk set {fields} on {count} components").format(
            fields=fields_changed, count=len(self._selected))
        try:
            board.adapter.set_field_values_bulk(updates, description)
        except ValidationError as e:
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(str(e))
            return
        except Exception as e:
            logger.exception("Bulk field write failed")
            self.message_label.setStyleSheet(_ERROR_STYLE)
            self.message_label.setText(_("Write failed: {error}").format(error=e))
            return

        self.message_label.setStyleSheet(_SUCCESS_STYLE)
        self.message_label.setText(
            _("Applied {fields} to {count} components.").format(
                fields=fields_changed, count=len(self._selected)))

    def _find_conflicts(self, board: Board, new_role: Optional[str],
                         new_cluster: Optional[str]) -> List[str]:
        """Role must not repeat within a Cluster (see extract_template_
        from_selection's identical "role appears twice in selection" check,
        and clone_role_resolver's whole-board role/cluster resolution — both
        rely on this holding). Checks two things: (1) the batch being
        applied doesn't create a duplicate among ITSELF, (2) the resulting
        (Role, Cluster) doesn't already exist on some OTHER component not in
        this batch. Exact-string comparison on both fields, not the
        segment-prefix match anchor_cluster resolution uses elsewhere — this
        is a precise "would this collide" check, not a "does this match a
        broader region" one."""
        selected_refs = {s.ref for s in self._selected}
        resultant = {}
        for s in self._selected:
            role = new_role if new_role is not None else s.role
            cluster = new_cluster if new_cluster is not None else s.cluster
            resultant[s.ref] = (role, cluster)

        conflicts = []
        seen = {}
        for ref, (role, cluster) in resultant.items():
            if role is None:
                continue
            key = (role, cluster)
            if key in seen:
                conflicts.append(
                    _("{a} and {b} would both have Role={role!r} in Cluster={cluster!r}")
                    .format(a=seen[key], b=ref, role=role, cluster=cluster))
            else:
                seen[key] = ref

        for other in board.select():
            if other.ref in selected_refs or not other.role:
                continue
            for ref, (role, cluster) in resultant.items():
                if role == other.role and cluster == other.cluster:
                    conflicts.append(
                        _("{ref} would duplicate existing {other}: Role={role!r} in Cluster={cluster!r}")
                        .format(ref=ref, other=other.ref, role=role, cluster=cluster))
        return conflicts
