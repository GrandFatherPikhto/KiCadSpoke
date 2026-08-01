# fieldstool/gui/tree.py
"""
ComponentTreeDock — Role/Cluster tree, browsing the SCHEMATIC (via
fieldstool.schema_model.SchematicComponent), not a live PCB snapshot.
Same UX shape as gui/docks/role_cluster_tree.py (group by Role/Cluster,
search + regex filter, expand/collapse state preserved across rebuilds)
— deliberately close to a port of that dock, data source swapped.

Two click behaviors, both via callbacks MainWindow wires (same "pick
from a list, don't retype" pattern kicadstamp/gui already uses):
  - a LEAF click -> on_leaf_picked(refs) — MainWindow may use this to
    highlight the matching footprint(s) on the live PCB (best-effort,
    only if connected — this dock has no live connection itself).
  - a GROUP node click (grouped by Role or Cluster) ->
    on_group_picked(field, value, refs) — feeds a "stage a rename for
    this whole group" action (fieldstool/gui/pending.py).
"""
import logging
import re
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, QItemSelectionModel
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QHBoxLayout,
                              QLineEdit, QPushButton, QTreeView, QVBoxLayout,
                              QWidget)

from kicadstamp.i18n import _

from ..schema_model import SchematicComponent

logger = logging.getLogger(__name__)

_REF_ROLE = Qt.ItemDataRole.UserRole + 1
_GROUP_FIELD_ROLE = Qt.ItemDataRole.UserRole + 2
_GROUP_VALUE_ROLE = Qt.ItemDataRole.UserRole + 3

_INVALID_REGEX_STYLE = "background-color: #ffcccc;"


class ComponentTreeDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__(_("Components"), main_window)
        self._main_window = main_window
        self._components: List[SchematicComponent] = []
        self._auto_expand_pending = True
        self.on_leaf_picked: Optional[Callable[[List[str]], None]] = None
        self.on_group_picked: Optional[Callable[[str, str, List[str]], None]] = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self._on_clicked)

        top_row = QHBoxLayout()
        self.group_by = QComboBox()
        self.group_by.addItems([_("Role"), _("Cluster")])
        self.group_by.currentIndexChanged.connect(self._rebuild)
        top_row.addWidget(self.group_by)
        self.collapse_all_button = QPushButton(_("Collapse all"))
        self.collapse_all_button.clicked.connect(self.tree.collapseAll)
        top_row.addWidget(self.collapse_all_button)
        layout.addLayout(top_row)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(_("Filter (ref/role/cluster)..."))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._rebuild)
        search_row.addWidget(self.search_edit)
        self.regex_checkbox = QCheckBox(_("regex"))
        self.regex_checkbox.toggled.connect(self._rebuild)
        search_row.addWidget(self.regex_checkbox)
        layout.addLayout(search_row)

        layout.addWidget(self.tree)

        self.setWidget(container)

    # ── Data in ──────────────────────────────────────────────────────────

    def set_components(self, components: List[SchematicComponent]) -> None:
        self._components = components
        self._auto_expand_pending = True
        self._rebuild()

    def highlight_refs(self, refs) -> None:
        """Reflects an externally-known selection (e.g. the live PCB
        selection, cross-probed from Eeschema — see
        fieldstool/gui/main_window.py) into the tree."""
        model = self.tree.model()
        if model is None:
            return
        selection_model = self.tree.selectionModel()
        selection_model.clearSelection()
        if not refs:
            return

        def walk(item: QStandardItem, ancestors):
            index = model.indexFromItem(item)
            ref = item.data(_REF_ROLE)
            if ref is not None and ref in refs:
                selection_model.select(index, QItemSelectionModel.SelectionFlag.Select)
                for a in ancestors:
                    self.tree.setExpanded(a, True)
                self.tree.scrollTo(index)
            for row in range(item.rowCount()):
                walk(item.child(row), ancestors + [index])

        root = model.invisibleRootItem()
        for row in range(root.rowCount()):
            walk(root.child(row), [])

    # ── Filtering ────────────────────────────────────────────────────────

    def _filtered(self) -> List[SchematicComponent]:
        query = self.search_edit.text()
        if not query:
            self.search_edit.setStyleSheet("")
            return self._components
        if self.regex_checkbox.isChecked():
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error:
                self.search_edit.setStyleSheet(_INVALID_REGEX_STYLE)
                return self._components
            self.search_edit.setStyleSheet("")
            return [c for c in self._components if self._regex_matches(c, pattern)]
        self.search_edit.setStyleSheet("")
        needle = query.lower()
        return [c for c in self._components if self._substring_matches(c, needle)]

    @staticmethod
    def _regex_matches(c: SchematicComponent, pattern: "re.Pattern") -> bool:
        return bool(pattern.search(c.ref) or (c.role and pattern.search(c.role))
                    or (c.cluster and pattern.search(c.cluster)))

    @staticmethod
    def _substring_matches(c: SchematicComponent, needle: str) -> bool:
        return (needle in c.ref.lower() or (c.role is not None and needle in c.role.lower())
                or (c.cluster is not None and needle in c.cluster.lower()))

    # ── Build / view-state preservation (same discipline as
    #    gui/docks/role_cluster_tree.py — a rebuild swaps in a brand new
    #    model, which would silently wipe selection/expansion otherwise) ──

    def _rebuild(self) -> None:
        expanded_paths, selected_refs = self._capture_view_state()
        visible = self._filtered()
        model = QStandardItemModel()
        field = "Role" if self.group_by.currentIndex() == 0 else "Cluster"
        key = (lambda c: c.role) if field == "Role" else (lambda c: c.cluster)
        self._build_flat(model, visible, field, key)
        self.tree.setModel(model)
        self._restore_view_state(expanded_paths, selected_refs)

    def _capture_view_state(self):
        model = self.tree.model()
        if model is None:
            return set(), set()
        selection_model = self.tree.selectionModel()
        expanded_paths = set()
        selected_refs = set()

        def walk(item: QStandardItem, path):
            index = model.indexFromItem(item)
            if self.tree.isExpanded(index):
                expanded_paths.add(path)
            if selection_model.isSelected(index):
                ref = item.data(_REF_ROLE)
                if ref is not None:
                    selected_refs.add(ref)
            for row in range(item.rowCount()):
                child = item.child(row)
                walk(child, path + (child.text(),))

        root = model.invisibleRootItem()
        for row in range(root.rowCount()):
            child = root.child(row)
            walk(child, (child.text(),))
        return expanded_paths, selected_refs

    def _restore_view_state(self, expanded_paths, selected_refs) -> None:
        model = self.tree.model()
        selection_model = self.tree.selectionModel()

        def walk(item: QStandardItem, path):
            index = model.indexFromItem(item)
            if path in expanded_paths:
                self.tree.setExpanded(index, True)
            ref = item.data(_REF_ROLE)
            if ref is not None and ref in selected_refs:
                selection_model.select(index, QItemSelectionModel.SelectionFlag.Select)
            for row in range(item.rowCount()):
                child = item.child(row)
                walk(child, path + (child.text(),))

        root = model.invisibleRootItem()
        for row in range(root.rowCount()):
            child = root.child(row)
            walk(child, (child.text(),))
        has_rows = model.invisibleRootItem().rowCount() > 0
        if not expanded_paths and self._auto_expand_pending and has_rows:
            self.tree.expandToDepth(0)
        if has_rows:
            self._auto_expand_pending = False

    @staticmethod
    def _leaf_item(c: SchematicComponent) -> QStandardItem:
        text = c.ref + (" ⚠" if c.divergent else "")  # warn on multi-unit divergence
        item = QStandardItem(text)
        item.setEditable(False)
        item.setData(c.ref, _REF_ROLE)
        if c.divergent:
            item.setToolTip(_("This refdes' units disagree on Role/Cluster — edit carefully."))
        return item

    def _build_flat(self, model: QStandardItemModel, items: List[SchematicComponent],
                     field: str, key) -> None:
        groups = {}
        for c in items:
            groups.setdefault(key(c) or _("(none)"), []).append(c)
        root = model.invisibleRootItem()
        for name in sorted(groups):
            members = groups[name]
            group_item = QStandardItem(f"{name} ({len(members)})")
            group_item.setEditable(False)
            group_item.setData(None, _REF_ROLE)
            group_item.setData(field, _GROUP_FIELD_ROLE)
            group_item.setData(name, _GROUP_VALUE_ROLE)
            for c in sorted(members, key=lambda c: c.ref):
                group_item.appendRow(self._leaf_item(c))
            root.appendRow(group_item)

    # ── Clicks ───────────────────────────────────────────────────────────

    def _on_clicked(self, index) -> None:
        item = self.tree.model().itemFromIndex(index)
        refs = self._collect_refs(item)
        ref = item.data(_REF_ROLE)
        if ref is None:
            field = item.data(_GROUP_FIELD_ROLE)
            value = item.data(_GROUP_VALUE_ROLE)
            if self.on_group_picked is not None and field is not None:
                self.on_group_picked(field, value, refs)
        elif self.on_leaf_picked is not None:
            self.on_leaf_picked(refs)

    def _collect_refs(self, item: QStandardItem) -> List[str]:
        ref = item.data(_REF_ROLE)
        if ref is not None:
            return [ref]
        refs: List[str] = []
        for row in range(item.rowCount()):
            refs.extend(self._collect_refs(item.child(row)))
        return refs
