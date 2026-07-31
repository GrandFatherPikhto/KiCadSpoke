# gui/docks/file_picker.py
"""
FilePickerDock — a read-only file tree for picking a YAML/JSON config file
by clicking instead of typing a path by hand. Standalone for now: nothing
in the GUI consumes the picked path yet (the extract-to-file dock that
would use it is postponed) — deliberately scoped to just browse, click,
remember the last pick.

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
from typing import Optional

from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import (QDockWidget, QFileDialog, QHBoxLayout, QLabel,
                              QPushButton, QTreeView, QVBoxLayout, QWidget)

from kicadstamp.i18n import _

from .. import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = PROJECT_ROOT / "boards"


class FilePickerDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__(_("Files"), main_window)
        self._main_window = main_window
        self.picked_path: Optional[Path] = None

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

        self.picked_label = QLabel(_("No file picked"))
        self.picked_label.setWordWrap(True)
        layout.addWidget(self.picked_label)

        self.setWidget(container)

        self.set_root(self._load_root())
        self._restore_last_pick()

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
        self.picked_label.setText(self._display_path(path))
        data = settings.load()
        data["last_picked_path"] = str(path)
        settings.save(data)

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
        self.picked_label.setText(self._display_path(path))
        index = self.model.index(str(path))
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)
        parent = index.parent()
        while parent.isValid():
            self.tree.expand(parent)
            parent = parent.parent()
