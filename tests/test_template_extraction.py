#!/usr/bin/env python3
"""Тесты на template_extraction.py и adapter.get_selected_items()."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock
from kipy.geometry import Vector2, Angle
from kipy.board_types import FootprintInstance, Via, Group

from kicadspoke.template_extraction import extract_template_from_selection
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.exceptions import ValidationError

MM = 1_000_000


def _make_fp(ref, x_mm, y_mm, angle_deg, role, pad_nets=None):
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    fp.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    fp.orientation = Angle.from_degrees(angle_deg)
    fp._role = role
    fp._pad_nets = pad_nets or []  # см. _make_adapter_with_pads
    return fp


def _make_adapter(footprints, vias=()):
    """Общий adapter-мок: get_field_value читает fp._role, get_footprint_pads
    строит по fp._pad_nets (список имён цепей -> список фейковых Pad)."""
    adapter = MagicMock()
    adapter.get_selected_items.return_value = list(footprints) + list(vias)
    adapter.get_field_value.side_effect = lambda fp, name: fp._role

    def _pads(fp):
        pads = []
        for net_name in fp._pad_nets:
            pad = MagicMock()
            pad.net.name = net_name
            pads.append(pad)
        return pads

    adapter.get_footprint_pads.side_effect = _pads
    return adapter


def _make_via(x_mm, y_mm, net_name, drill_mm=0.3, diameter_mm=0.6):
    v = MagicMock(spec=Via)
    v.position = Vector2.from_xy(int(x_mm * MM), int(y_mm * MM))
    v.net.name = net_name
    v.drill_diameter = int(drill_mm * MM)
    v.diameter = int(diameter_mm * MM)
    return v


class TestExtractTemplateFromSelection:
    def test_crystal_with_three_roles_and_via(self):
        xtal = _make_fp("Y2", 10.0, 10.0, 0.0, "XTAL")
        cap1 = _make_fp("C15", 8.0, 12.0, 90.0, "LOAD_CAP_1")
        cap2 = _make_fp("C16", 12.0, 12.0, 270.0, "LOAD_CAP_2")
        via1 = _make_via(10.0, 8.0, "GND")

        adapter = MagicMock()
        adapter.get_selected_items.return_value = [xtal, cap1, cap2, via1]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role

        result = extract_template_from_selection(adapter, "crystal_8mhz")
        tpl = result["crystal_8mhz"]
        assert len(tpl["components"]) == 3
        assert len(tpl["vias"]) == 1

        xtal_c = next(c for c in tpl["components"] if c["role"] == "XTAL")
        assert xtal_c["offset_along_mm"] == 2.0
        assert xtal_c["offset_across_mm"] == -2.0
        assert xtal_c["angle_deg"] == 0.0

        assert tpl["vias"][0]["offset_along_mm"] == 2.0
        assert tpl["vias"][0]["offset_across_mm"] == -4.0
        assert tpl["vias"][0]["net"] == "GND"

    def test_empty_selection_raises(self):
        adapter = MagicMock()
        adapter.get_selected_items.return_value = []
        with pytest.raises(ValidationError):
            extract_template_from_selection(adapter, "t")

    def test_missing_role_raises_with_ref(self):
        fp = _make_fp("C5", 0, 0, 0, None)
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [fp]
        adapter.get_field_value.return_value = None
        with pytest.raises(ValidationError, match="C5"):
            extract_template_from_selection(adapter, "t")

    def test_duplicate_role_raises_with_both_refs(self):
        fp1 = _make_fp("C5", 0, 0, 0, "HEAVY")
        fp2 = _make_fp("C6", 1, 1, 0, "HEAVY")
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [fp1, fp2]
        adapter.get_field_value.side_effect = lambda fp, name: fp._role
        with pytest.raises(ValidationError, match="HEAVY"):
            extract_template_from_selection(adapter, "t")

    def test_non_footprint_non_via_items_ignored_not_fatal(self):
        """Что-то ещё в выделении (например, зона) -- игнорируется, не фатально,
        если рядом есть хотя бы один валидный футпринт/via."""
        fp = _make_fp("C5", 0, 0, 0, "SOLO")
        stray_item = MagicMock()  # не Footprint и не Via
        adapter = MagicMock()
        adapter.get_selected_items.return_value = [fp, stray_item]
        adapter.get_field_value.side_effect = lambda f, name: getattr(f, "_role", None)

        result = extract_template_from_selection(adapter, "t")
        assert len(result["t"]["components"]) == 1


class TestGetSelectedItems:
    def test_group_expanded_via_proto_items(self):
        adapter = KiCadBoardAdapter.__new__(KiCadBoardAdapter)

        member_uuid = MagicMock()
        member_uuid.value = "fp-uuid-1"
        group = MagicMock(spec=Group)
        group.proto.items = [member_uuid]
        group.items = []  # с сервера всегда пусто -- не должно использоваться

        fp_in_group = MagicMock(spec=FootprintInstance)
        fp_in_group.id.value = "fp-uuid-1"
        fp_direct = MagicMock(spec=FootprintInstance)
        fp_direct.id.value = "fp-uuid-2"
        via_direct = MagicMock(spec=Via)
        via_direct.id.value = "via-uuid-1"

        board = MagicMock()
        board.get_selection.return_value = [group, fp_direct, via_direct]
        board.get_footprints.return_value = [fp_in_group, fp_direct]
        board.get_vias.return_value = [via_direct]
        adapter._board = board

        items = adapter.get_selected_items()
        assert len(items) == 3
        assert fp_in_group in items
        assert fp_direct in items
        assert via_direct in items


class TestNetTemplateAutoDetect:
    """net_template по одной совпавшей цепи из net_template_map — без
    net_template_role, старое поведение (регрессия)."""

    def test_single_matching_net_sets_net_template(self):
        cap = _make_fp("C1", 0, 0, 0, "C_IN_BULK", pad_nets=["+5V_DIRTY", "GND"])
        adapter = _make_adapter([cap])

        result = extract_template_from_selection(
            adapter, "t", params={"PWR_IN": "+5V_DIRTY"},
            net_template_map={"+5V_DIRTY": "{PWR_IN}"},
        )
        comp = result["t"]["components"][0]
        assert comp["net_template"] == "{PWR_IN}"

    def test_two_matching_nets_leaves_net_template_unset_with_warning(self, caplog):
        fb = _make_fp("FB1", 0, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([fb])

        result = extract_template_from_selection(
            adapter, "t", params={"PWR_IN": "+5V_DIRTY", "PWR_OUT": "+5V"},
            net_template_map={"+5V_DIRTY": "{PWR_IN}", "+5V": "{PWR_OUT}"},
        )
        comp = result["t"]["components"][0]
        assert "net_template" not in comp
        assert "цепей из --net-template" in caplog.text


class TestNetTemplateRole:
    """net_template_role — явное указание, какую из нескольких цепей на
    падах компонента считать net_template этой роли (см. диалог про
    PI_FILTER_FB/дроссели на стыке двух рельсов)."""

    def test_resolves_ambiguous_component_explicitly(self):
        fb = _make_fp("FB1", 0, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([fb])

        result = extract_template_from_selection(
            adapter, "t", params={"PWR_IN": "+5V_DIRTY", "PWR_OUT": "+5V"},
            net_template_map={"+5V_DIRTY": "{PWR_IN}", "+5V": "{PWR_OUT}"},
            net_template_role={"PI_FILTER_FB": "+5V_DIRTY"},
        )
        comp = result["t"]["components"][0]
        assert comp["net_template"] == "{PWR_IN}"

    def test_fatal_if_requested_net_not_on_pads(self):
        fb = _make_fp("FB1", 0, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([fb])

        with pytest.raises(ValidationError):
            extract_template_from_selection(
                adapter, "t", params={"PWR_IN": "+5V_DIRTY"},
                net_template_map={"+5V_DIRTY": "{PWR_IN}"},
                net_template_role={"PI_FILTER_FB": "+3V3"},  # такой цепи на падах нет
            )

    def test_fatal_if_literal_missing_from_net_template_map(self):
        fb = _make_fp("FB1", 0, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([fb])

        with pytest.raises(ValidationError):
            extract_template_from_selection(
                adapter, "t", params={},
                net_template_map={},  # +5V_DIRTY нигде не зарегистрирован
                net_template_role={"PI_FILTER_FB": "+5V_DIRTY"},
            )

    def test_role_not_in_net_template_role_uses_auto_detect(self):
        """Компонент, для которого net_template_role не задан, продолжает
        резолвиться старым (авто) путём — новая опция не ломает соседей."""
        cap = _make_fp("C1", 0, 0, 0, "C_IN_BULK", pad_nets=["+5V_DIRTY", "GND"])
        fb = _make_fp("FB1", 5, 0, 0, "PI_FILTER_FB", pad_nets=["+5V_DIRTY", "+5V"])
        adapter = _make_adapter([cap, fb])

        result = extract_template_from_selection(
            adapter, "t", params={"PWR_IN": "+5V_DIRTY", "PWR_OUT": "+5V"},
            net_template_map={"+5V_DIRTY": "{PWR_IN}", "+5V": "{PWR_OUT}"},
            net_template_role={"PI_FILTER_FB": "+5V_DIRTY"},
        )
        by_role = {c["role"]: c for c in result["t"]["components"]}
        assert by_role["C_IN_BULK"]["net_template"] == "{PWR_IN}"
        assert by_role["PI_FILTER_FB"]["net_template"] == "{PWR_IN}"
