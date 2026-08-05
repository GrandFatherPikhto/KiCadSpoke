# tests/gui/test_detail_panel.py
from gui.docks.detail_panel import DetailDock
from gui.docks.extract import ExtractDock
from gui.docks.placer import PlacerDock
from gui.docks.points import PointsDock
from gui.docks.root_metadata import RootMetadataDock
from gui.docks.thermal_via import ThermalViaArrayDock


def test_pages_are_the_expected_panel_types(main_window):
    dock = DetailDock(main_window)
    assert isinstance(dock.extract_panel, ExtractDock)
    assert isinstance(dock.placer_panel, PlacerDock)
    assert isinstance(dock.root_panel, RootMetadataDock)
    assert isinstance(dock.thermal_via_panel, ThermalViaArrayDock)
    assert isinstance(dock.points_panel, PointsDock)
    assert dock.stack.count() == 5


def test_extract_tab_is_shown_first(main_window):
    dock = DetailDock(main_window)
    assert dock.tab_bar.currentIndex() == 0
    assert dock.stack.currentWidget() is dock.extract_panel


def test_show_placer_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_placer()
    assert dock.tab_bar.currentIndex() == 1
    assert dock.stack.currentWidget() is dock.placer_panel


def test_show_root_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_root()
    assert dock.tab_bar.currentIndex() == 2
    assert dock.stack.currentWidget() is dock.root_panel


def test_show_extract_switches_back(main_window):
    dock = DetailDock(main_window)
    dock.show_root()
    dock.show_extract()
    assert dock.tab_bar.currentIndex() == 0
    assert dock.stack.currentWidget() is dock.extract_panel


def test_manually_clicking_a_tab_switches_the_stack(main_window):
    """Manual override — the tab bar itself, not just the auto-switch
    methods, must drive the stack (2026-08-03: Denis chose auto + manual
    selector so a panel stays reachable even without a matching tree
    click)."""
    dock = DetailDock(main_window)
    dock.tab_bar.setCurrentIndex(2)
    assert dock.stack.currentWidget() is dock.root_panel


def test_show_thermal_via_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_thermal_via()
    assert dock.tab_bar.currentIndex() == 3
    assert dock.stack.currentWidget() is dock.thermal_via_panel


def test_show_points_switches_tab_and_stack(main_window):
    dock = DetailDock(main_window)
    dock.show_points()
    assert dock.tab_bar.currentIndex() == 4
    assert dock.stack.currentWidget() is dock.points_panel
