# gui/docks/file_picker.py
"""
FilePickerDock — a read-only file tree for picking a YAML/JSON config file
by clicking instead of typing a path by hand. Standalone for now: nothing
in the GUI consumes the picked path yet (the extract-to-file dock that
would use it is postponed) — deliberately scoped to just browse, click,
remember the last pick.

Root is boards/ (this project's own config tree), NOT derived from the
live board connection. Checked live before assuming that: kipy's
Project.path (via KiCad.get_project(board.document)) points at wherever
the open .kicad_pro actually lives — on this machine that's
test_boards/3CH-AWG-TIA/, a separate sandbox tree entirely from the
checked-in boards/3ch-awg-tia/profiles this tool actually cares about.
Auto-deriving the root from the live connection would have silently
pointed at the wrong tree.
"""
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import QDockWidget, QLabel, QTreeView, QVBoxLayout, QWidget

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

        self.set_root(DEFAULT_ROOT if DEFAULT_ROOT.is_dir() else PROJECT_ROOT)
        self._restore_last_pick()

    def set_root(self, root: Path) -> None:
        self.model.setRootPath(str(root))
        self.tree.setRootIndex(self.model.index(str(root)))

    def _on_clicked(self, index) -> None:
        path = Path(self.model.filePath(index))
        if path.is_dir():
            return
        self.picked_path = path
        self._update_picked_label()
        data = settings.load()
        data["last_picked_path"] = str(path)
        settings.save(data)

    def _update_picked_label(self) -> None:
        try:
            shown = str(self.picked_path.relative_to(PROJECT_ROOT))
        except ValueError:
            shown = str(self.picked_path)
        self.picked_label.setText(shown)

    def _restore_last_pick(self) -> None:
        """Restores the last file picked in a previous session — skipped
        (not an error) if it's gone since then (deleted/renamed/moved
        outside boards/), since a stale remembered path is just not useful
        anymore, not a problem to report."""
        last = settings.load().get("last_picked_path")
        if not last:
            return
        path = Path(last)
        if not path.is_file():
            return
        self.picked_path = path
        self._update_picked_label()
        index = self.model.index(str(path))
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)
        parent = index.parent()
        while parent.isValid():
            self.tree.expand(parent)
            parent = parent.parent()
