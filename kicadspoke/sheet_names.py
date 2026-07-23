# kicadspoke/sheet_names.py
"""
sheet_names.py — словарь {uuid: Sheetname} прямым парсингом *.kicad_sch
(sexpdata, тот же формат, что уже читает kicadspoke.cloner). НЕ через
kipy — см. обсуждение в чате:
  - sheet_path.path_human_readable сломан в этой версии KiCad (всегда
    пустая строка, поле есть в протоколе, но не заполняется).
  - Прямое сопоставление UUID из fp.sheet_path.path с uuid из (sheet ...)
    блоков .kicad_sch — эмпирически ПОДТВЕРЖДЕНО: path[:-1] (без
    последнего элемента — это, предположительно, собственный uuid
    символа) один в один совпадает с цепочкой листов из файлов схемы,
    без единого конфликта на реальном проекте (mishin-coil, 331
    футпринт, 15 групп, 0 конфликтов, 0 нерасшифрованных uuid).

Используется только для ClonePlacement.anchor_sheet (сужение
неоднозначности anchor_role) — вообще не нужен, если anchor_sheet
никем в конфиге не используется.
"""
import glob
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import sexpdata

from .exceptions import ValidationError, format_fatal_error

logger = logging.getLogger(__name__)


def _children(node, tag: str) -> List[list]:
    if not isinstance(node, list):
        return []
    return [n for n in node[1:] if isinstance(n, list) and n and str(n[0]) == tag]


def _parse_sheet_uuids(path: str) -> Dict[str, str]:
    """{uuid: Sheetname} из всех (sheet ...) блоков ОДНОГО файла .kicad_sch."""
    result = {}
    try:
        with open(path, encoding='utf-8') as f:
            data = sexpdata.load(f)
    except Exception as e:
        logger.warning(f"не удалось распарсить {path} как .kicad_sch: {type(e).__name__}: {e} — "
                       f"пропущен, словарь sheet_names будет неполным")
        return result
    for sheet in _children(data, 'sheet'):
        uuid_nodes = _children(sheet, 'uuid')
        uuid_val = str(uuid_nodes[0][1]) if uuid_nodes else None
        name = None
        for prop in _children(sheet, 'property'):
            if len(prop) > 1 and str(prop[1]) == 'Sheetname':
                name = str(prop[2])
        if uuid_val and name:
            result[uuid_val] = name
    return result


def build_sheet_name_map(config_path: str, schematic_dir: Optional[str],
                         schematic_files: List[str]) -> Dict[str, str]:
    """
    Собирает {uuid: Sheetname} из schematic_dir (все *.kicad_sch внутри,
    не рекурсивно — так же, как watchdog в netexp) + schematic_files
    (точечные добавки для листов "на отшибе"). Оба пути — относительно
    самого YAML-конфига (config_path), как и templates_file.

    Пусто, если ни то, ни другое не задано — это НЕ ошибка сама по себе,
    ошибка (фатал) — только если потом реально понадобится anchor_sheet,
    а словарь пуст (см. validation.py).
    """
    base = Path(config_path).parent
    files = []

    if schematic_dir:
        d = base / schematic_dir
        if not d.is_dir():
            raise ValidationError(format_fatal_error(
                f"schematic_dir {schematic_dir!r} не найден",
                [f"ожидалась директория {d} (относительно самого конфига {config_path!r})"]
            ))
        files.extend(glob.glob(str(d / "*.kicad_sch")))

    for extra in (schematic_files or []):
        p = base / extra
        if not p.exists():
            raise ValidationError(format_fatal_error(
                f"schematic_files: файл {extra!r} не найден",
                [f"ожидался по пути {p} (относительно самого конфига {config_path!r})"]
            ))
        files.append(str(p))

    result: Dict[str, str] = {}
    for f in files:
        result.update(_parse_sheet_uuids(f))

    if files:
        logger.info(f"sheet_names: {len(files)} файлов .kicad_sch просканировано, "
                   f"{len(result)} листов в словаре")
    return result


def resolve_sheet_path_names(fp, sheet_names: Dict[str, str]) -> List[Optional[str]]:
    """
    fp.sheet_path.path[:-1] (без последнего — своего uuid символа),
    переведено через словарь в человекочитаемые имена. None на позиции,
    если конкретный uuid не нашёлся в словаре (например, schematic_dir
    указывает не туда, или лист переименован/удалён после сборки
    словаря) — вызывающий код должен уметь честно сказать "не совпало",
    а не тихо считать None подходящим сегментом.
    """
    path_uuids = [str(u.value) for u in fp.sheet_path.path]
    chain = path_uuids[:-1]
    return [sheet_names.get(u) for u in chain]