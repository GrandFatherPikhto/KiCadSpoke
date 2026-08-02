# gui/docks/file_picker.py
"""
FilePickerDock — a read-only file tree for picking a YAML/JSON config file
by clicking instead of typing a path by hand, plus three named "role"
slots (Cells / Extractor / Placer) that other docks read their target file
from. Before this, ExtractDock had its OWN separate file-dialog button for
the extract_profiles file, alongside this dock's tree — two different ways
to pick a file for closely related purposes, reported as confusing live
2026-08-01 ("получается каша с выбором"). Now there's exactly one place to
pick any file this GUI writes to: click a file in the tree, then "Use
selected" on whichever role it belongs to.

All three roles are shareable with each other — all are the same
"structured root config" shape (extract_profiles:/cells:/include:/
clone_placements: as sibling keys, since cells_file:/cell_files: were
folded into include: 2026-08-02 — see
handoff_2026_08_02_cells_include_unification.md), and pointing more than
one role at the same file works fine (see ExtractDock, which writes
include: entries into whatever the Placer file is right after an
extract). Nothing stops all three from being the same file if that suits
a small board; a dedicated file per role is just the default habit, not a
requirement enforced anywhere.

Default root is boards/ (this project's own config tree), NOT derived from
the live board connection. Checked live before assuming that would work:
kipy's Project.path (via KiCad.get_project(board.document)) points at
wherever the open .kicad_pro actually lives — on this machine that's
test_boards/3CH-AWG-TIA/, a separate sandbox tree entirely from the
checked-in boards/3ch-awg-tia/profiles this tool actually cares about.
Auto-deriving the root from the live connection would have silently
pointed at the wrong tree — so instead it's a plain "Change root..."
picker (QFileDialog), remembered in gui/gui_state.json (root_dir) across
restarts, same mechanism as the last-picked-file memory below.
"""
import logging
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import (QDockWidget, QFileDialog, QHBoxLayout, QLabel,
                              QPushButton, QTreeView, QVBoxLayout, QWidget)

from kicadstamp.i18n import _

from .. import settings
from ._common import PROJECT_ROOT, display_path

logger = logging.getLogger(__name__)

DEFAULT_ROOT = PROJECT_ROOT / "boards"
ROLE_KEYS = ("cells", "extractor", "placer")


class FilePickerDock(QDockWidget):
    # Fired when "Use selected" assigns a file to a role, and again by
    # restore_roles() when a previous session's assignment is restored —
    # MainWindow wires every dock that follows that role (see
    # gui/main_window.py). Payload is Optional[Path] (None when the role
    # is unset).
    cells_file_changed = pyqtSignal(object)
    extractor_file_changed = pyqtSignal(object)
    placer_file_changed = pyqtSignal(object)

    def __init__(self, main_window):
        super().__init__(_("Files"), main_window)
        self._main_window = main_window
        self.picked_path: Optional[Path] = None  # last file clicked in the tree, not yet assigned to a role
        self.assigned: Dict[str, Optional[Path]] = {key: None for key in ROLE_KEYS}
        self._role_titles = {"cells": _("Cells:"), "extractor": _("Extractor:"), "placer": _("Placer:")}

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        root_row = QHBoxLayout()
        self.root_label = QLabel("")
        self.root_label.setWordWrap(True)
        root_row.addWidget(self.root_label, 1)
        change_root_button = QPushButton(_("Change root..."))
        change_root_button.clicked.connect(self._on_change_root)
        root_row.addWidget(change_root_button)
        layout.addLayout(root_row)

        self.model = QFileSystemModel()
        self.model.setNameFilters(["*.yaml", "*.yml", "*.json"])
        self.model.setNameFilterDisables(False)  # hide non-matching files, don't just grey them out

        self.tree = QTreeView()
        self.tree.setModel(self.model)
        for column in (1, 2, 3):  # size / type / date modified — keep just the name column
            self.tree.hideColumn(column)
        self.tree.clicked.connect(self._on_clicked)
        layout.addWidget(self.tree)

        self.picked_label = QLabel(_("No file selected"))
        self.picked_label.setWordWrap(True)
        layout.addWidget(self.picked_label)

        self._role_labels: Dict[str, QLabel] = {}
        for role_key in ROLE_KEYS:
            row = QHBoxLayout()
            label = QLabel("")
            label.setWordWrap(True)
            self._role_labels[role_key] = label
            row.addWidget(label, 1)
            button = QPushButton(_("Use selected"))
            button.clicked.connect(lambda checked=False, k=role_key: self._assign_role(k))
            row.addWidget(button)
            layout.addLayout(row)

        self.setWidget(container)

        self.set_root(self._load_root())
        self._restore_last_pick()

    @staticmethod
    def _load_root() -> Path:
        saved = settings.state.get("root_dir")
        if saved:
            path = Path(saved)
            if path.is_dir():
                return path
            logger.warning("Remembered root_dir %r no longer exists, falling back to default", saved)
        return DEFAULT_ROOT if DEFAULT_ROOT.is_dir() else PROJECT_ROOT

    def set_root(self, root: Path) -> None:
        self.model.setRootPath(str(root))
        self.tree.setRootIndex(self.model.index(str(root)))
        self.root_label.setText(display_path(root))

    def _on_change_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, _("Pick config root directory"), self.model.rootPath())
        if not chosen:
            return
        self.set_root(Path(chosen))
        settings.state.set("root_dir", chosen)

    def _on_clicked(self, index) -> None:
        path = Path(self.model.filePath(index))
        if path.is_dir():
            return
        self.picked_path = path
        self.picked_label.setText(_("Selected: {path}").format(path=display_path(path)))
        settings.state.set("last_picked_path", str(path))

    def _assign_role(self, role_key: str) -> None:
        """"Use selected" button for one of the three role rows — assigns
        whatever's currently clicked in the tree to that role, persists it,
        and notifies every listener via the {role_key}_file_changed signal."""
        if self.picked_path is None:
            return
        self.assigned[role_key] = self.picked_path
        self._role_labels[role_key].setText(self._role_text(role_key, self.picked_path))
        settings.state.set(f"{role_key}_file", str(self.picked_path))
        getattr(self, f"{role_key}_file_changed").emit(self.picked_path)

    def _role_text(self, role_key: str, path: Optional[Path]) -> str:
        value = display_path(path) if path is not None else _("not set")
        return f"{self._role_titles[role_key]} {value}"

    def restore_roles(self) -> None:
        """Re-reads the three role assignments from the previous session and
        re-pushes them through the *_file_changed signals. Called by
        MainWindow AFTER connecting the signal listeners — a restored
        assignment exists before those listeners do, so without this
        re-push it would be silently missed (see gui/main_window.py)."""
        data = settings.load()
        for role_key in ROLE_KEYS:
            saved = data.get(f"{role_key}_file")
            path = Path(saved) if saved and Path(saved).is_file() else None
            self.assigned[role_key] = path
            self._role_labels[role_key].setText(self._role_text(role_key, path))
            getattr(self, f"{role_key}_file_changed").emit(path)

    def _restore_last_pick(self) -> None:
        """Restores the last file picked in a previous session — skipped
        (not an error) if it's gone since then (deleted, renamed, or the
        root directory changed since), since a stale remembered path is
        just not useful anymore, not a problem to report."""
        last = settings.state.get("last_picked_path")
        if not last:
            return
        path = Path(last)
        if not path.is_file():
            return
        self.picked_path = path
        self.picked_label.setText(_("Selected: {path}").format(path=display_path(path)))
        index = self.model.index(str(path))
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)
        parent = index.parent()
        while parent.isValid():
            self.tree.expand(parent)
            parent = parent.parent()
