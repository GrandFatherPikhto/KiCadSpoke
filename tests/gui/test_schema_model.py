# tests/gui/test_schema_model.py
from gui.schema_model import load_schematic_components
from tests.fieldstool_fixtures import sch_file, symbol_block


def test_one_row_per_ref_simple(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R1"], role="R_A", cluster="Cl_A")), encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert len(comps) == 1
    assert comps[0].ref == "R1" and comps[0].role == "R_A" and comps[0].cluster == "Cl_A"
    assert comps[0].divergent is False


def test_multi_instance_block_expands_to_one_row_per_ref(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["R41", "R50", "R59"], role="R_A")), encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert {c.ref for c in comps} == {"R41", "R50", "R59"}
    assert all(c.role == "R_A" for c in comps)


def test_multi_unit_ref_collapses_to_one_row(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["U1"], role="OA_A"), symbol_block(["U1"], role="OA_A")),
                     encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert len(comps) == 1
    assert comps[0].ref == "U1" and comps[0].divergent is False


def test_multi_unit_divergent_values_are_flagged(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["U1"], role="OA_A"), symbol_block(["U1"], role="OA_B")),
                     encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert len(comps) == 1
    assert comps[0].divergent is True


def test_component_with_no_role_or_cluster(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(symbol_block(["C1"])), encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert comps[0].role is None and comps[0].cluster is None


def test_power_symbols_are_excluded(tmp_path):
    """Refs starting with "#" (power symbols/PWR_FLAG, e.g. #PWR01, #FLG01)
    are KiCad's own "excluded from board" convention — no footprint ever
    exists for them, so they'd otherwise show up as pure noise (found live
    2026-08-03: 171 unfilterable "#FLG*" rows on a real board)."""
    root = tmp_path / "root.kicad_sch"
    root.write_text(sch_file(
        symbol_block(["R1"], role="R_A"),
        symbol_block(["#FLG01"]),
        symbol_block(["#PWR01"]),
    ), encoding="utf-8")

    comps = load_schematic_components(str(root))

    assert {c.ref for c in comps} == {"R1"}


def test_components_across_multiple_sheets(tmp_path):
    child = tmp_path / "child.kicad_sch"
    child.write_text(sch_file(symbol_block(["C1"], role="C_A")), encoding="utf-8")
    root = tmp_path / "root.kicad_sch"
    from tests.fieldstool_fixtures import sheet_block
    root.write_text(sch_file(symbol_block(["R1"], role="R_A"), sheet_block("child.kicad_sch")),
                     encoding="utf-8")

    comps = load_schematic_components(str(root))
    assert {c.ref for c in comps} == {"R1", "C1"}
