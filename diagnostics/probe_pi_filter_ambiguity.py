#!/usr/bin/env python3
"""
probe_pi_filter_ambiguity.py — для заданных refdes печатает Role, Cluster и
полный человекочитаемый путь по листам схемы (тот же механизм, что
resolve_sheet_path_names/_fp_on_sheet в clone_role_resolver.py) — чтобы
понять, почему anchor_sheet/anchor_cluster не сужают неоднозначных
кандидатов до одного (см. FATAL ERROR ambiguity в выводе --apply, список
кандидатов там же).

Запуск: python diagnostics/probe_pi_filter_ambiguity.py [REF ...]
(без аргументов — дефолт C139/C143/C148)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kicadspoke.config import load_config
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.sheet_names import resolve_sheet_path_names
from kicadspoke.constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME

CONFIG_PATH = str(Path(__file__).resolve().parents[1] / "boards" / "3ch-awg-tia" / "3ch-awg-tia.yaml")
DEFAULT_TARGET_REFS = {"C139", "C143", "C148"}


def main():
    target_refs = set(sys.argv[1:]) or DEFAULT_TARGET_REFS
    cfg = load_config(CONFIG_PATH)
    print(f"sheet_names: {len(cfg.sheet_names)} записей\n")

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()

    for fp in adapter.get_footprints():
        ref = fp.reference_field.text.value
        if ref not in target_refs:
            continue
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
        names = resolve_sheet_path_names(fp, cfg.sheet_names)
        pads = adapter.get_footprint_pads(fp)
        nets = sorted({p.net.name for p in pads if p.net and p.net.name})
        print(f"{ref}:")
        print(f"  Role={role!r}  Cluster={cluster!r}")
        print(f"  sheet path (человекочитаемо): {names}")
        print(f"  nets: {nets}")
        print()


if __name__ == "__main__":
    main()
