# gui/docks/role_cluster_tree.py
"""
RoleClusterTreeDock — groups the live footprint snapshot by Role or by
Cluster and highlights the picked component(s) on the real board on click.
First real panel of the GUI (see gui/main_window.py's docstring for why this
one first: validates the whole IPC -> model -> UI -> highlight-on-board
chain on the simplest possible, read-only case before anything writes to
the board).

Cluster grouping is a real nested tree, split on '/' (Channel_1/PI_FILTER),
matching the segment-hierarchy _cluster_prefix_match's callers already rely
on elsewhere (see kicadstamp/explore.py's Board.select() docstring) — NOT a
flat group on the exact string, which would put Channel_0/PI_FILTER and
Channel_1/PI_FILTER in unrelated top-level buckets instead of showing the
shared structure.
"""
import logging
import re
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QHBoxLayout,
                              QLineEdit, QTreeView, QVBoxLayout, QWidget)

from kicadstamp.explore import Selected
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

# Leaf items carry their refdes here; group items carry None — _collect_refs
# below tells the two apart by this, not by row-count/child-count guessing.
_REF_ROLE = Qt.ItemDataRole.UserRole + 1

_INVALID_REGEX_STYLE = "background-color: #ffcccc;"


class RoleClusterTreeDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__(_("Components"), main_window)
        self._main_window = main_window
        self._selected: List[Selected] = []

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.group_by = QComboBox()
        self.group_by.addItems([_("Role"), _("Cluster")])
        self.group_by.currentIndexChanged.connect(self._rebuild)
        layout.addWidget(self.group_by)

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

        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self._on_clicked)
        layout.addWidget(self.tree)

        self.setWidget(container)

    def set_footprints(self, selected: List[Selected]) -> None:
        """Called by MainWindow after every successful poll/refresh with the
        full current snapshot (Board.select() with no filters)."""
        self._selected = selected
        self._rebuild()

    def _rebuild(self) -> None:
        """Called on every poll tick (via set_footprints), on group-by
        toggle, and on every search-box keystroke — a brand new
        QStandardItemModel is built and swapped in each time (simplest way
        to reflect additions/removals/renames, and to drop now-empty groups
        after filtering), which by itself would silently clear the tree's
        own selection/expansion state even though nothing the user did
        changed. Snapshot both before the swap, by refdes/path (stable
        across rebuilds as long as the underlying grouping didn't change),
        and restore them after."""
        expanded_paths, selected_refs = self._capture_view_state()
        visible = self._filtered_selected()
        model = QStandardItemModel()
        if self.group_by.currentIndex() == 0:  # Role
            self._build_flat(model, visible, key=lambda s: s.role)
        else:  # Cluster
            self._build_hierarchical(model, visible, key=lambda s: s.cluster)
        self.tree.setModel(model)
        self._restore_view_state(expanded_paths, selected_refs)

    def _filtered_selected(self) -> List[Selected]:
        """Search box matches against ref/role/cluster (OR — typing a role
        name and typing a refdes are both "find the thing" the same way).
        Empty query -> everything, no filter. Regex mode is case-insensitive
        for the same reason plain-text mode is: this is a quick "find it",
        not a precise pattern tool."""
        query = self.search_edit.text()
        if not query:
            self.search_edit.setStyleSheet("")
            return self._selected

        if self.regex_checkbox.isChecked():
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error:
                # Invalid/incomplete regex while typing — flag it, don't
                # crash and don't hide everything mid-keystroke.
                self.search_edit.setStyleSheet(_INVALID_REGEX_STYLE)
                return self._selected
            self.search_edit.setStyleSheet("")
            return [s for s in self._selected if self._regex_matches(s, pattern)]

        self.search_edit.setStyleSheet("")
        needle = query.lower()
        return [s for s in self._selected if self._substring_matches(s, needle)]

    @staticmethod
    def _regex_matches(s: Selected, pattern: "re.Pattern") -> bool:
        return bool(pattern.search(s.ref) or (s.role and pattern.search(s.role))
                    or (s.cluster and pattern.search(s.cluster)))

    @staticmethod
    def _substring_matches(s: Selected, needle: str) -> bool:
        return (needle in s.ref.lower() or (s.role is not None and needle in s.role.lower())
                or (s.cluster is not None and needle in s.cluster.lower()))

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
        if not expanded_paths:
            # First build (or everything was collapsed) — start with
            # top-level groups visible instead of a single flat blob.
            self.tree.expandToDepth(0)

    @staticmethod
    def _leaf_item(s: Selected) -> QStandardItem:
        item = QStandardItem(s.ref)
        item.setEditable(False)
        item.setData(s.ref, _REF_ROLE)
        return item

    def _build_flat(self, model: QStandardItemModel, items: List[Selected],
                     key: Callable[[Selected], Optional[str]]) -> None:
        groups = {}
        for s in items:
            groups.setdefault(key(s) or _("(none)"), []).append(s)
        root = model.invisibleRootItem()
        for name in sorted(groups):
            members = groups[name]
            group_item = QStandardItem(f"{name} ({len(members)})")
            group_item.setEditable(False)
            group_item.setData(None, _REF_ROLE)
            for s in sorted(members, key=lambda s: s.ref):
                group_item.appendRow(self._leaf_item(s))
            root.appendRow(group_item)

    def _build_hierarchical(self, model: QStandardItemModel, items: List[Selected],
                             key: Callable[[Selected], Optional[str]]) -> None:
        root = model.invisibleRootItem()
        nodes = {(): root}  # path tuple (segments so far) -> QStandardItem
        for s in sorted(items, key=lambda s: (key(s) or "", s.ref)):
            cluster = key(s)
            segments = tuple(cluster.split("/")) if cluster else (_("(none)"),)
            for depth in range(1, len(segments) + 1):
                path = segments[:depth]
                if path in nodes:
                    continue
                parent = nodes[segments[:depth - 1]]
                node = QStandardItem(segments[depth - 1])
                node.setEditable(False)
                node.setData(None, _REF_ROLE)
                parent.appendRow(node)
                nodes[path] = node
            nodes[segments].appendRow(self._leaf_item(s))

    def _on_clicked(self, index) -> None:
        item = self.tree.model().itemFromIndex(index)
        refs = set(self._collect_refs(item))
        board = self._main_window.connection.board
        if board is None or not refs:
            return
        footprints = [s.fp for s in self._selected if s.ref in refs]
        board.adapter.select_items(footprints)

    def _collect_refs(self, item: QStandardItem) -> List[str]:
        """Leaf items answer with their own refdes; group items answer with
        every refdes under them, so clicking a group (e.g. a whole Cluster
        branch) highlights all of it on the board, not just one component."""
        ref = item.data(_REF_ROLE)
        if ref is not None:
            return [ref]
        refs: List[str] = []
        for row in range(item.rowCount()):
            refs.extend(self._collect_refs(item.child(row)))
        return refs
