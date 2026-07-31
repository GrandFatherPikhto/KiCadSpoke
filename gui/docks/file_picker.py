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

Extractor and Placer are meant to be shareable — both are the same
"structured root config" shape (extract_profiles:/cell_files:/include:/
clone_placements: as sibling keys), and pointing both at one file is the
normal way to use this (see ExtractDock, which writes cell_files:/
include: entries into whatever the Placer file is right after an
extract). Cells is NOT shareable with either: cell_files/cells_file
content is parsed as a FLAT {cell_name: {...}} dict with no wrapper (see
config/loader.py) — every top-level key is read as a cell name, so an
extract_profiles:/cell_files: key sharing that file would itself be
misread as a cell. _update_role_warning() flags that combination rather
than silently letting it happen.

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
from typing import Callable, Dict, Optional

from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import (QDockWidget, QFileDialog, QHBoxLayout, QLabel,
                              QPushButton, QTreeView, QVBoxLayout, QWidget)

from kicadstamp.i18n import _

from .. import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = PROJECT_ROOT / "boards"
ROLE_KEYS = ("cells", "extractor", "placer")


class FilePickerDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__(_("Files"), main_window)
        self._main_window = main_window
        self.picked_path: Optional[Path] = None  # last file clicked in the tree, not yet assigned to a role
        self.assigned: Dict[str, Optional[Path]] = {key: None for key in ROLE_KEYS}
        self._role_titles = {"cells": _("Cells:"), "extractor": _("Extractor:"), "placer": _("Placer:")}
        # Set by MainWindow once the relevant dock exists — ExtractDock's
        # cell-output file follows the Cells role, its extract_profiles
        # file follows the Extractor role. Plain callbacks, not
        # pyqtSignals: at most one listener each, and this codebase doesn't
        # use custom signals anywhere else yet.
        self.on_cells_file_changed: Optional[Callable[[Optional[Path]], None]] = None
        self.on_extractor_file_changed: Optional[Callable[[Optional[Path]], None]] = None
        self.on_placer_file_changed: Optional[Callable[[Optional[Path]], None]] = None

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

        self.role_warning_label = QLabel("")
        self.role_warning_label.setWordWrap(True)
        self.role_warning_label.setStyleSheet("color: #a00;")
        layout.addWidget(self.role_warning_label)

        self.setWidget(container)

        self.set_root(self._load_root())
        self._restore_last_pick()
        self._restore_roles()
        self._update_role_warning()

    @staticmethod
    def _load_root() -> Path:
        saved = settings.load().get("root_dir")
        if saved:
            path = Path(saved)
            if path.is_dir():
                return path
            logger.warning("Remembered root_dir %r no longer exists, falling back to default", saved)
        return DEFAULT_ROOT if DEFAULT_ROOT.is_dir() else PROJECT_ROOT

    def set_root(self, root: Path) -> None:
        self.model.setRootPath(str(root))
        self.tree.setRootIndex(self.model.index(str(root)))
        self.root_label.setText(self._display_path(root))

    def _on_change_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, _("Pick config root directory"), self.model.rootPath())
        if not chosen:
            return
        self.set_root(Path(chosen))
        data = settings.load()
        data["root_dir"] = chosen
        settings.save(data)

    def _on_clicked(self, index) -> None:
        path = Path(self.model.filePath(index))
        if path.is_dir():
            return
        self.picked_path = path
        self.picked_label.setText(_("Selected: {path}").format(path=self._display_path(path)))
        data = settings.load()
        data["last_picked_path"] = str(path)
        settings.save(data)

    def _assign_role(self, role_key: str) -> None:
        """"Use selected" button for one of the three role rows — assigns
        whatever's currently clicked in the tree to that role, persists it,
        and notifies whichever dock is listening (see on_*_file_changed)."""
        if self.picked_path is None:
            return
        self.assigned[role_key] = self.picked_path
        self._role_labels[role_key].setText(self._role_text(role_key, self.picked_path))
        data = settings.load()
        data[f"{role_key}_file"] = str(self.picked_path)
        settings.save(data)
        self._update_role_warning()
        callback = getattr(self, f"on_{role_key}_file_changed")
        if callback:
            callback(self.picked_path)

    def _update_role_warning(self) -> None:
        cells = self.assigned["cells"]
        if cells is not None and cells in (self.assigned["extractor"], self.assigned["placer"]):
            self.role_warning_label.setText(
                _("Cells shares a file with Extractor/Placer — every top-level key in a "
                  "Cells file is read as a cell name, so extract_profiles:/cell_files: "
                  "entries in the same file would corrupt it. Give Cells its own file."))
        else:
            self.role_warning_label.setText("")

    def _role_text(self, role_key: str, path: Optional[Path]) -> str:
        value = self._display_path(path) if path is not None else _("not set")
        return f"{self._role_titles[role_key]} {value}"

    def _restore_roles(self) -> None:
        data = settings.load()
        for role_key in ROLE_KEYS:
            saved = data.get(f"{role_key}_file")
            path = Path(saved) if saved and Path(saved).is_file() else None
            self.assigned[role_key] = path
            self._role_labels[role_key].setText(self._role_text(role_key, path))

    @staticmethod
    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(path)

    def _restore_last_pick(self) -> None:
        """Restores the last file picked in a previous session — skipped
        (not an error) if it's gone since then (deleted, renamed, or the
        root directory changed since), since a stale remembered path is
        just not useful anymore, not a problem to report."""
        last = settings.load().get("last_picked_path")
        if not last:
            return
        path = Path(last)
        if not path.is_file():
            return
        self.picked_path = path
        self.picked_label.setText(_("Selected: {path}").format(path=self._display_path(path)))
        index = self.model.index(str(path))
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)
        parent = index.parent()
        while parent.isValid():
            self.tree.expand(parent)
            parent = parent.parent()
