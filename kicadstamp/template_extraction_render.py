# kicadstamp/template_extraction_render.py
"""
template_extraction_render.py — text-based YAML post-processing for template
extraction. Split out of template_extraction.py during the T3.1 god-file
decomposition (behavior-preserving code move — see
handoff_2026_08_05_architecture_fixes_roadmap.md).

render_uncertain_comments() annotates the yaml.dump() output from cmd_extract
with commented-out placeholder lines, scoped to one `name:` cell block.
"""
import re


def render_uncertain_comments(yaml_text: str, name: str,
                               annotations: list[tuple[str, str, str]],
                               indent: int = 0) -> str:
    """
    Post-processes yaml.dump() output for cmd_extract: for every
    (role, field, hint) in annotations, inserts a commented-out placeholder
    line ("# field: hint") right after the component block for that role,
    scoped to the section under the `name:` key only — cmd_extract's
    `existing` dict may hold OTHER, previously extracted cells in the same
    output file, and a role from THIS extraction must never accidentally
    match a same-named role belonging to a different cell.

    indent — leading spaces before the `name:` key itself. 0 when yaml_text
    is genuinely top-level; 2 for cmd_extract's actual on-disk shape since
    2026-08-02 (cells_file:/cell_files: folded into include:, so `name:` now
    sits one level under a wrapping 'cells:' key — see
    handoff_2026_08_02_cells_include_unification.md). List items/their own
    fields shift by the same amount (indent+2 / indent+4).

    Text-based, not a YAML-aware round-trip: the only producer of this text
    is our own yaml.dump(sort_keys=False, default_flow_style=False) call in
    cmd_extract, so the indentation shape is known and stable — a full
    round-trip library (ruamel.yaml) would be overkill for annotating
    output we generate ourselves.
    """
    if not annotations:
        return yaml_text
    lines = yaml_text.splitlines(keepends=True)

    def body(line: str) -> str:
        return line.rstrip("\n")

    prefix = " " * indent
    name_pattern = re.compile(r'^' + prefix + r'(["\']?)' + re.escape(name) + r'\1:\s*$')
    start = None
    for i, line in enumerate(lines):
        if name_pattern.match(body(line)):
            start = i + 1
            break
    if start is None:
        return yaml_text
    end = len(lines)
    for i in range(start, len(lines)):
        b = body(lines[i])
        leading = len(b) - len(b.lstrip(' '))
        if b.strip() and leading <= indent:
            end = i
            break

    block = lines[start:end]
    for role, field, hint in annotations:
        role_pattern = re.compile(r'^' + prefix + r'  - role: (["\']?)' + re.escape(role) + r'\1\s*$')
        role_idx = next((i for i, line in enumerate(block)
                         if role_pattern.match(body(line))), None)
        if role_idx is None:
            continue
        insert_at = len(block)
        for i in range(role_idx + 1, len(block)):
            b = body(block[i])
            leading = len(b) - len(b.lstrip(' '))
            if b.strip() and leading < indent + 4:
                insert_at = i
                break
        block.insert(insert_at, " " * (indent + 4) + f"# {field}: {hint}\n")

    return "".join(lines[:start] + block + lines[end:])
