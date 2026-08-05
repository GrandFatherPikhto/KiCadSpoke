# gui/docks/detail_panel.py
"""
DetailDock — one shared right-hand form area for Extract/Placer/Root/
Thermal via (2026-08-03, GUI tree roadmap: Denis — "Панели: Экстракт,
Пласер, Рут — становятся контекстными (общая область формы)"). Used to be
separate QDockWidgets tabified together; ExtractDock/PlacerDock/
RootMetadataDock/ThermalViaArrayDock are now plain QWidgets (see each
module's own docstring) living as pages of one QStackedWidget here,
switched by a QTabBar that drives the stack directly (the standard "tabs
without their own page-widgets" Qt pattern — a QTabWidget would insist on
owning/parenting the pages itself).

Switching is BOTH automatic (Config-tree context) and manual (the tab bar
itself) — Denis picked this over auto-only when asked live 2026-08-03,
specifically so a panel stays reachable even when the tree click that
would normally select it hasn't happened (e.g. checking Root while
Extract is what's currently showing). gui/dock_hub.py wires the automatic
half: cell_picked/placement_picked/add_placer_requested -> show_placer(),
profile_picked -> show_extract(), thermal_via_picked/add_thermal_via_
requested -> show_thermal_via() (added same day, "для thermal_via_array я
бы сразу создал панель"), file_selected (fires on EVERY click, including
leaf clicks, always BEFORE the more specific signal — see config_tree.py's
_on_clicked) -> show_root() as the fallback for a plain file/category
click; the more specific signal's handler (if any) then runs right after
and wins.
"""
from PyQt6.QtWidgets import (QDockWidget, QStackedWidget, QTabBar, QVBoxLayout, QWidget)

from kicadstamp.i18n import _

from .extract import ExtractDock
from .placer import PlacerDock
from .points import PointsDock
from .root_metadata import RootMetadataDock
from .rules import RuleDock
from .thermal_via import ThermalViaArrayDock

_EXTRACT, _PLACER, _ROOT, _THERMAL_VIA, _POINTS, _RULES = range(6)


class DetailDock(QDockWidget):
    def __init__(self, main_window, connection=None):
        super().__init__(_("Detail"), main_window)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.tab_bar = QTabBar()
        self.tab_bar.addTab(_("Extract"))
        self.tab_bar.addTab(_("Placer"))
        # Displayed as "Project" (2026-08-05, Denis: "давай не root, а
        # project") — the underlying panel is still RootMetadataDock/
        # root_metadata_dock/show_root() (root_panel here below): "root"
        # remains the correct internal term (it edits the project's ROOT
        # config file, same concept ConfigTreeDock's "Open Root file..."
        # uses), only the user-facing label changed.
        self.tab_bar.addTab(_("Project"))
        self.tab_bar.addTab(_("Thermal via"))
        self.tab_bar.addTab(_("Points"))
        self.tab_bar.addTab(_("Rules"))
        layout.addWidget(self.tab_bar)

        self.stack = QStackedWidget()
        self.extract_panel = ExtractDock(main_window, connection=connection)
        self.placer_panel = PlacerDock(main_window)
        self.root_panel = RootMetadataDock(main_window)
        self.thermal_via_panel = ThermalViaArrayDock(main_window)
        self.points_panel = PointsDock(main_window, connection=connection)
        self.rules_panel = RuleDock(main_window)
        self.stack.addWidget(self.extract_panel)
        self.stack.addWidget(self.placer_panel)
        self.stack.addWidget(self.root_panel)
        self.stack.addWidget(self.thermal_via_panel)
        self.stack.addWidget(self.points_panel)
        self.stack.addWidget(self.rules_panel)
        layout.addWidget(self.stack)

        self.tab_bar.currentChanged.connect(self.stack.setCurrentIndex)

        self.setWidget(container)

    def show_extract(self) -> None:
        self.tab_bar.setCurrentIndex(_EXTRACT)

    def show_placer(self) -> None:
        self.tab_bar.setCurrentIndex(_PLACER)

    def show_root(self) -> None:
        self.tab_bar.setCurrentIndex(_ROOT)

    def show_thermal_via(self) -> None:
        self.tab_bar.setCurrentIndex(_THERMAL_VIA)

    def show_points(self) -> None:
        self.tab_bar.setCurrentIndex(_POINTS)

    def show_rules(self) -> None:
        self.tab_bar.setCurrentIndex(_RULES)
