# kicadspoke/config/includes.py
"""
includes.py — generic `include:` for splitting one profile YAML into several
files (e.g. by subsystem: ldo.yaml, pi_filters.yaml, dac_channels.yaml — each
carrying whatever mix of extract_profiles/clone_placements/rules/templates
that subsystem needs).

Independent of templates_file (kicadspoke/config/loader.py) — that mechanism
stays as-is (single-purpose, templates only, inline-overrides-external).
include: is general-purpose and used by BOTH load_config() (rules/
clone_placements/templates) and load_profile() in kicadspoke_cli.py
(extract_profiles/clone_profiles) — the two existing, otherwise-independent
YAML-reading entry points — since a subsystem file is meant to carry
extract_profiles AND clone_placements together, not just one section.

Operates on raw dicts (already yaml.safe_load'd), before any Config/dataclass
parsing — resolve_includes() is called right after yaml.safe_load() in both
load_config() and load_profile(), and everything downstream is unchanged.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import yaml

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

logger = logging.getLogger(__name__)

# List sections: concatenated (this file's own entries first, then each
# include's, in listed order). YAML order has no functional effect —
# dependency_order.py already reorders rules/clone_placements by real anchor
# dependency at apply time.
_LIST_SECTIONS = ('rules', 'clone_placements')

# Dict sections: merged key-by-key, fatal on a key defined in two different
# files (unlike templates_file's silent inline-overrides-external — these are
# meant to be genuinely separate subsystem files, so a repeated key is far
# more likely a mistake than an intentional override).
_DICT_SECTIONS = ('templates', 'extract_profiles', 'clone_profiles')


def _parse_include_entry(entry: Any, source_path: str) -> Tuple[str, bool]:
    if isinstance(entry, str):
        return entry, True
    if isinstance(entry, dict) and 'path' in entry:
        return entry['path'], bool(entry.get('enabled', True))
    raise ValidationError(format_fatal_error(
        _("include: invalid entry {entry!r} in {source!r}").format(entry=entry, source=source_path),
        [_("each entry must be either a file path string, or a mapping "
           "{{path: <str>, enabled: <bool>}}")]
    ))


def _resolve(path: str, data: Dict[str, Any], seen: Set[Path]) -> Dict[str, Any]:
    base_dir = Path(path).parent
    if not isinstance(data, dict):
        raise ValidationError(format_fatal_error(
            _("{file!r}: top level must be a YAML mapping, got {type}").format(
                file=path, type=type(data).__name__),
            [_("check for a stray/misplaced list (e.g. list items left without "
               "a wrapping 'clone_placements:'/'rules:' key)")]
        ))

    for section in _LIST_SECTIONS:
        raw = data.get(section)
        if raw is not None and not isinstance(raw, list):
            raise ValidationError(format_fatal_error(
                _("{file!r}: {section!r} must be a list, got {type}").format(
                    file=path, section=section, type=type(raw).__name__),
                [_("{section!r} entries are a YAML list ('- name: ...'); "
                   "{dict_sections} are mappings — check for a mixed-up "
                   "section key").format(section=section, dict_sections=_DICT_SECTIONS)]
            ))
    for section in _DICT_SECTIONS:
        raw = data.get(section)
        if raw is not None and not isinstance(raw, dict):
            raise ValidationError(format_fatal_error(
                _("{file!r}: {section!r} must be a mapping, got {type}").format(
                    file=path, section=section, type=type(raw).__name__),
                [_("{section!r} entries are a YAML mapping ('key: {{...}}'); "
                   "{list_sections} are lists — check for a mixed-up "
                   "section key").format(section=section, list_sections=_LIST_SECTIONS)]
            ))

    merged: Dict[str, Any] = {}

    for section in _LIST_SECTIONS:
        merged[section] = list(data.get(section) or [])
    for section in _DICT_SECTIONS:
        merged[section] = dict(data.get(section) or {})
    for key, value in data.items():
        if key in _LIST_SECTIONS or key in _DICT_SECTIONS or key == 'include':
            continue
        merged[key] = value

    for entry in data.get('include', []) or []:
        include_str, enabled = _parse_include_entry(entry, path)
        if not enabled:
            logger.info(_("include: {file!r} disabled, skipped (not opened)").format(file=include_str))
            continue

        include_path = (base_dir / include_str).resolve()
        if not include_path.exists():
            raise ValidationError(format_fatal_error(
                _("include: file {file!r} not found").format(file=include_str),
                [_("expected at {path} (relative to {source!r}, not the current "
                   "working directory)").format(path=include_path, source=path)]
            ))
        if include_path in seen:
            raise ValidationError(format_fatal_error(
                _("include: {file!r} is included more than once (directly or "
                  "indirectly), from {source!r}").format(file=str(include_path), source=path),
                [_("either a cycle, or the same file included from two different "
                   "branches — both are unsupported, split the shared content out "
                   "instead")]
            ))
        seen.add(include_path)

        with open(include_path, 'r', encoding='utf-8') as f:
            include_data = yaml.safe_load(f) or {}
        include_merged = _resolve(str(include_path), include_data, seen)

        for section in _LIST_SECTIONS:
            merged[section].extend(include_merged.get(section) or [])
        for section in _DICT_SECTIONS:
            for key, value in (include_merged.get(section) or {}).items():
                if key in merged[section]:
                    raise ValidationError(format_fatal_error(
                        _("include: duplicate {section} key {key!r}").format(section=section, key=key),
                        [_("defined in {a!r} and again via include {b!r}")
                         .format(a=path, b=include_str)]
                    ))
                merged[section][key] = value

        logger.info(_("include: merged {file!r} into {source!r}").format(file=include_str, source=path))

    return merged


def resolve_includes(path: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively resolves data['include'] (if present), merging list sections
    (rules/clone_placements — concatenated) and dict sections (templates/
    extract_profiles/clone_profiles — merged, fatal on key collision) from
    every included file into data. Returns a new dict; does not mutate data.

    path — the file data was loaded from (used to resolve relative include
    paths, and to seed cycle/diamond detection with the root itself).
    """
    seen: Set[Path] = {Path(path).resolve()}
    return _resolve(path, data, seen)
