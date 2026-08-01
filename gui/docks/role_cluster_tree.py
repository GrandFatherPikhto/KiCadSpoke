# gui/docks/role_cluster_tree.py
"""
RoleClusterTreeDock — groups either the live PCB footprint snapshot or
fieldstool's parsed-schematic component list by Role or by Cluster, and
highlights/routes the picked component(s) accordingly. First real panel of
the GUI (see gui/main_window.py's docstring for why this one first:
validates the whole IPC -> model -> UI -> highlight-on-board chain on the
simplest possible, read-only case before anything writes to the board).

Two modes, toggled by the "Not yet applied" checkbox:
- Live (default, unchecked) — today's original behavior: data comes from
  kicadstamp.explore.Selected (live PCB footprints), a click pushes the
  selection onto the real board, and (Cluster grouping only) a group click
  fires on_cluster_picked() for PlacerDock.
- Schematic ("not yet applied", checked) — data comes from fieldstool's
  already-parsed SchematicComponent list (gui/docks/fieldstool_dock.py's
  embedded fieldstool MainWindow, read fresh at every rebuild, never
  cached here) — for picking a fieldstool target without needing a live
  board selection, the same job fieldstool's own now-deleted internal tree
  used to do. A click calls straight into fieldstool's existing
  _on_tree_leaf_picked()/_on_group_picked() (reusing its staging/combo-fill
  logic verbatim) and brings the fieldstool tab to front.

Both modes share one filter/build/view-state-preservation pipeline —
normalized into a small _Row(ref, role, cluster, divergent) so the tree
itself doesn't need two families of build methods. Cluster grouping is a
real nested tree in both modes, split on '/' (Channel_1/PI_FILTER),
matching the segment-hierarchy _cluster_prefix_match's callers already
rely on elsewhere (see kicadstamp/explore.py's Board.select() docstring)
— NOT a flat group on the exact string (fieldstool's old tree used to do
that for Cluster; this is a deliberate, approved behavior change to match
this dock's existing Cluster handling instead).
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QHBoxLayout,
                              QLineEdit, QPushButton, QTreeView, QVBoxLayout,
                              QWidget)

from kicadstamp.explore import Selected
from kicadstamp.i18n import _

from .. import settings

logger = logging.getLogger(__name__)

# Leaf items carry their refdes here; group items carry None — _collect_refs
# below tells the two apart by this, not by row-count/child-count guessing.
_REF_ROLE = Qt.ItemDataRole.UserRole + 1
# Every group item (flat top-level or hierarchical intermediate node) carries
# its own bare (flat)/full '/'-joined (hierarchical) value here — reading
# this directly avoids re-deriving it from displayed text, which has i18n
# "(none)"/"(count)" decoration baked in for flat groups.
_GROUP_VALUE_ROLE = Qt.ItemDataRole.UserRole + 2

_INVALID_REGEX_STYLE = "background-color: #ffcccc;"


@dataclass
class _Row:
    ref: str
    role: Optional[str]
    cluster: Optional[str]
    divergent: bool = field(default=False)


class RoleClusterTreeDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__(_("Components"), main_window)
        self._main_window = main_window
        self._selected: List[Selected] = []
        # Distinguishes "first build with actual data" (auto-expand top
        # level so the tree isn't a single flat blob) from "user just
        # collapsed everything via the button" (both leave
        # _capture_view_state's expanded_paths empty, but only the former
        # should re-expand) — _rebuild() runs on every ~2s poll tick, so
        # without this flag Collapse all would snap back open on the very
        # next tick. Only consumed once a rebuild actually has rows: the
        # group_by combo's persisted setting can itself trigger an empty
        # _rebuild() during __init__ (before the first set_footprints()),
        # and that empty build must not burn the flag before real data
        # ever gets a chance to auto-expand.
        self._auto_expand_pending = True
        # Fired when a Cluster GROUP node is clicked while grouped by
        # Cluster, in LIVE mode only (see _on_clicked) — PlacerDock
        # listens, so picking a cluster here fills its Cluster field the
        # same way picking a cell elsewhere fills the Cell field. Not
        # fired for Role-mode or leaf clicks, and not fired at all in
        # schematic mode (that mode routes into fieldstool instead — see
        # module docstring).
        self.on_cluster_picked: Optional[Callable[[str], None]] = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        top_row = QHBoxLayout()
        self.group_by = QComboBox()
        self.group_by.addItems([_("Role"), _("Cluster")])
        self.group_by.setCurrentIndex(settings.load().get("tree_group_by", 0))
        self.group_by.currentIndexChanged.connect(self._on_group_by_changed)
        top_row.addWidget(self.group_by)
        self.collapse_all_button = QPushButton(_("Collapse all"))
        self.collapse_all_button.clicked.connect(self.tree_collapse_all)
        top_row.addWidget(self.collapse_all_button)
        layout.addLayout(top_row)

        # NOT restored from settings here — see restore_mode_from_settings()
        # below for why (main_window.fieldstool_dock doesn't exist yet at
        # this point in gui/main_window.py's __init__).
        self.mode_checkbox = QCheckBox(_("Not yet applied"))
        self.mode_checkbox.toggled.connect(self._on_mode_changed)
        layout.addWidget(self.mode_checkbox)

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

    def restore_mode_from_settings(self) -> None:
        """Call once, from gui/main_window.py, only after self._main_window's
        fieldstool_dock has been constructed — restoring "schematic mode"
        triggers a rebuild that reads main_window.fieldstool_dock.window,
        which doesn't exist yet during this dock's own __init__ (tree_dock
        is built before fieldstool_dock there)."""
        if settings.load().get("tree_schematic_mode"):
            self.mode_checkbox.setChecked(True)  # triggers _on_mode_changed via its signal

    def set_footprints(self, selected: List[Selected]) -> None:
        """Called by MainWindow after every successful poll/refresh with the
        full current snapshot (Board.select() with no filters). Only
        rebuilds the tree if currently showing live data — must not
        clobber an active schematic view on every ~2s poll tick."""
        self._selected = selected
        if not self.mode_checkbox.isChecked():
            self._rebuild()

    def refresh_schematic_view(self) -> None:
        """Wired to fieldstool's on_components_changed hook (see
        gui/docks/fieldstool_dock.py) — an explicit Rescan/Apply-triggered
        schematic refresh updates this tree immediately if it's currently
        showing schematic data, instead of going stale until an unrelated
        event (search keystroke, mode toggle) happens to rebuild it."""
        if self.mode_checkbox.isChecked():
            self._rebuild()

    def highlight_board_selection(self, refs) -> None:
        """Reflects the live KiCad GUI selection into the tree — the reverse
        direction of _on_clicked (tree click -> board selection). Called
        frequently (see MainWindow's selection-watch timer), so unlike
        _rebuild() this never touches the model itself, only the selection
        (cheap) — and bails out early if the target refs already match
        what's currently selected, so an unchanged board selection doesn't
        cause any visible churn on every tick. Mode-agnostic: just matches
        against whatever _REF_ROLE data is in the currently active model."""
        model = self.tree.model()
        if model is None:
            return
        _, current_refs = self._capture_view_state()
        if current_refs == refs:
            return
        selection_model = self.tree.selectionModel()
        selection_model.clearSelection()
        if not refs:
            return

        def walk(item: QStandardItem, ancestor_indexes):
            index = model.indexFromItem(item)
            ref = item.data(_REF_ROLE)
            if ref is not None and ref in refs:
                selection_model.select(index, QItemSelectionModel.SelectionFlag.Select)
                for ancestor in ancestor_indexes:
                    self.tree.setExpanded(ancestor, True)
                self.tree.scrollTo(index)
            for row in range(item.rowCount()):
                walk(item.child(row), ancestor_indexes + [index])

        root = model.invisibleRootItem()
        for row in range(root.rowCount()):
            walk(root.child(row), [])

    def tree_collapse_all(self) -> None:
        self.tree.collapseAll()

    def _on_group_by_changed(self) -> None:
        data = settings.load()
        data["tree_group_by"] = self.group_by.currentIndex()
        settings.save(data)
        self._rebuild()

    def _on_mode_changed(self, checked: bool) -> None:
        data = settings.load()
        data["tree_schematic_mode"] = checked
        settings.save(data)
        self._rebuild()

    def _current_rows(self) -> List[_Row]:
        if not self.mode_checkbox.isChecked():
            return [_Row(s.ref, s.role, s.cluster) for s in self._selected]
        # Public accessor on fieldstool's MainWindow (see fieldstool/gui/
        # main_window.py's components property) — not the private
        # `_components`, which is refreshed wholesale and owned by that window.
        components = self._main_window.fieldstool_dock.window.components
        return [_Row(c.ref, c.role, c.cluster, divergent=c.divergent) for c in components]

    def _rebuild(self) -> None:
        """Called on every poll tick while in live mode (via set_footprints),
        on group-by/mode toggle, and on every search-box keystroke — a
        brand new QStandardItemModel is built and swapped in each time
        (simplest way to reflect additions/removals/renames, and to drop
        now-empty groups after filtering), which by itself would silently
        clear the tree's own selection/expansion state even though nothing
        the user did changed. Snapshot both before the swap, by refdes/path
        (stable across rebuilds as long as the underlying grouping didn't
        change), and restore them after."""
        expanded_paths, selected_refs = self._capture_view_state()
        visible = self._filtered(self._current_rows())
        model = QStandardItemModel()
        if self.group_by.currentIndex() == 0:  # Role
            self._build_flat(model, visible, key=lambda r: r.role)
        else:  # Cluster
            self._build_hierarchical(model, visible, key=lambda r: r.cluster)
        self.tree.setModel(model)
        self._restore_view_state(expanded_paths, selected_refs)

    def _filtered(self, rows: List[_Row]) -> List[_Row]:
        """Search box matches against ref/role/cluster (OR — typing a role
        name and typing a refdes are both "find the thing" the same way).
        Empty query -> everything, no filter. Regex mode is case-insensitive
        for the same reason plain-text mode is: this is a quick "find it",
        not a precise pattern tool."""
        query = self.search_edit.text()
        if not query:
            self.search_edit.setStyleSheet("")
            return rows

        if self.regex_checkbox.isChecked():
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error:
                # Invalid/incomplete regex while typing — flag it, don't
                # crash and don't hide everything mid-keystroke.
                self.search_edit.setStyleSheet(_INVALID_REGEX_STYLE)
                return rows
            self.search_edit.setStyleSheet("")
            return [r for r in rows if self._regex_matches(r, pattern)]

        self.search_edit.setStyleSheet("")
        needle = query.lower()
        return [r for r in rows if self._substring_matches(r, needle)]

    @staticmethod
    def _regex_matches(r: _Row, pattern: "re.Pattern") -> bool:
        return bool(pattern.search(r.ref) or (r.role and pattern.search(r.role))
                    or (r.cluster and pattern.search(r.cluster)))

    @staticmethod
    def _substring_matches(r: _Row, needle: str) -> bool:
        return (needle in r.ref.lower() or (r.role is not None and needle in r.role.lower())
                or (r.cluster is not None and needle in r.cluster.lower()))

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
            # First build with actual data — start with top-level groups
            # visible instead of a single flat blob. Once the user has
            # interacted with expansion (including collapsing everything
            # on purpose via the Collapse all button), later rebuilds must
            # respect that instead of forcing depth-0 back open every poll
            # tick.
            self.tree.expandToDepth(0)
        if has_rows:
            self._auto_expand_pending = False

    @staticmethod
    def _leaf_item(r: _Row) -> QStandardItem:
        text = r.ref + (" ⚠" if r.divergent else "")  # warn on multi-unit divergence (schematic mode)
        item = QStandardItem(text)
        item.setEditable(False)
        item.setData(r.ref, _REF_ROLE)
        if r.divergent:
            item.setToolTip(_("This refdes' units disagree on Role/Cluster — edit carefully."))
        return item

    def _build_flat(self, model: QStandardItemModel, items: List[_Row],
                     key: Callable[[_Row], Optional[str]]) -> None:
        groups = {}
        for r in items:
            groups.setdefault(key(r) or _("(none)"), []).append(r)
        root = model.invisibleRootItem()
        for name in sorted(groups):
            members = groups[name]
            group_item = QStandardItem(f"{name} ({len(members)})")
            group_item.setEditable(False)
            group_item.setData(None, _REF_ROLE)
            group_item.setData(name, _GROUP_VALUE_ROLE)
            for r in sorted(members, key=lambda r: r.ref):
                group_item.appendRow(self._leaf_item(r))
            root.appendRow(group_item)

    def _build_hierarchical(self, model: QStandardItemModel, items: List[_Row],
                             key: Callable[[_Row], Optional[str]]) -> None:
        root = model.invisibleRootItem()
        nodes = {(): root}  # path tuple (segments so far) -> QStandardItem
        for r in sorted(items, key=lambda r: (key(r) or "", r.ref)):
            cluster = key(r)
            segments = tuple(cluster.split("/")) if cluster else (_("(none)"),)
            for depth in range(1, len(segments) + 1):
                path = segments[:depth]
                if path in nodes:
                    continue
                parent = nodes[segments[:depth - 1]]
                node = QStandardItem(segments[depth - 1])
                node.setEditable(False)
                node.setData(None, _REF_ROLE)
                node.setData("/".join(path), _GROUP_VALUE_ROLE)
                parent.appendRow(node)
                nodes[path] = node
            nodes[segments].appendRow(self._leaf_item(r))

    def _on_clicked(self, index) -> None:
        item = self.tree.model().itemFromIndex(index)
        refs = set(self._collect_refs(item))
        is_group = item.data(_REF_ROLE) is None

        if not self.mode_checkbox.isChecked():
            if (self.group_by.currentIndex() == 1  # Cluster grouping
                    and is_group and self.on_cluster_picked is not None):
                self.on_cluster_picked(item.data(_GROUP_VALUE_ROLE))

            board = self._main_window.connection.board
            if board is None or not refs:
                return
            footprints = [s.fp for s in self._selected if s.ref in refs]
            board.adapter.select_items(footprints)
        else:
            fieldstool_window = self._main_window.fieldstool_dock.window
            if is_group:
                field_name = "Role" if self.group_by.currentIndex() == 0 else "Cluster"
                fieldstool_window._on_group_picked(field_name, item.data(_GROUP_VALUE_ROLE),
                                                   sorted(refs))
            else:
                fieldstool_window._on_tree_leaf_picked(sorted(refs))
            self._main_window.open_fieldstool()

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
