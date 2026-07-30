#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rename project from KiCadSpoke/kicadspoke to KiCadStamp/kicadstamp.

Usage: python tools/rename_project.py
"""

import os
import shutil
import re
import fnmatch
import sys

# Force UTF-8 output
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files/directories to skip
SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".claude",
    "__pycache__", "*.pyc", "*.pyo",
}
SKIP_FILES = {
    "desktop.ini",
}

LOCALES_DIR = os.path.join(BASE_DIR, "locales")


def should_skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".") and name != "."


def should_skip_file(name: str) -> bool:
    return name in SKIP_FILES


def is_binary(path: str) -> bool:
    """Check if file is binary by reading first few bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
            return b"\0" in chunk
    except Exception:
        return True


def replace_in_file(filepath: str) -> bool:
    """Replace kicadstamp -> kicadstamp and KiCadStamp -> KiCadStamp in a text file.
    Returns True if any replacements were made."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except (UnicodeDecodeError, PermissionError, OSError):
        return False

    # Order matters: replace longer/capitalized forms first to avoid double-replacement
    new_content = content.replace("KICADSPOKE", "KICADSTAMP")
    new_content = new_content.replace("KiCadSpoke", "KiCadStamp")
    new_content = new_content.replace("kicadspoke", "kicadstamp")

    if new_content != content:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True
    return False


def process_directory(root_dir: str) -> list:
    """Walk directory and replace text in all files. Returns list of modified files."""
    modified = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Filter out skipped dirs
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]

        for fn in filenames:
            if should_skip_file(fn):
                continue
            filepath = os.path.join(dirpath, fn)
            if is_binary(filepath):
                continue
            if replace_in_file(filepath):
                modified.append(os.path.relpath(filepath, BASE_DIR))
    return modified


def rename_locale_files():
    """Rename kicadstamp.po -> kicadstamp.po and kicadstamp.mo -> kicadstamp.mo in locale dirs."""
    renamed = []
    for lang_dir in os.listdir(LOCALES_DIR):
        lc_messages = os.path.join(LOCALES_DIR, lang_dir, "LC_MESSAGES")
        if not os.path.isdir(lc_messages):
            continue
        for ext in (".po", ".mo"):
            old_name = f"kicadspoke{ext}"
            new_name = f"kicadstamp{ext}"
            old_path = os.path.join(lc_messages, old_name)
            new_path = os.path.join(lc_messages, new_name)
            if os.path.exists(old_path):
                os.rename(old_path, new_path)
                renamed.append(os.path.relpath(new_path, BASE_DIR))
    return renamed


def rename_root_files():
    """Rename root-level files."""
    renames = []
    pairs = [
        ("kicadspoke_cli.py", "kicadstamp_cli.py"),
        ("kicadspoke_templates_example.yaml", "kicadstamp_templates_example.yaml"),
    ]
    for old, new in pairs:
        old_path = os.path.join(BASE_DIR, old)
        new_path = os.path.join(BASE_DIR, new)
        if os.path.exists(old_path):
            os.rename(old_path, new_path)
            renames.append((old, new))
    return renames


def rename_package_dir():
    """Rename kicadstamp/ -> kicadstamp/ at the base level."""
    old_dir = os.path.join(BASE_DIR, "kicadspoke")
    new_dir = os.path.join(BASE_DIR, "kicadstamp")
    if os.path.exists(old_dir) and not os.path.exists(new_dir):
        os.rename(old_dir, new_dir)
        return True
    return False


def main():
    print(f"Project base: {BASE_DIR}")

    # Step 1: Replace content in kicadstamp/ first (the main package)
    print("\n=== Step 1: Replacing text in kicadstamp/ package ===")
    pkg_dir = os.path.join(BASE_DIR, "kicadstamp")
    if os.path.isdir(pkg_dir):
        modified = process_directory(pkg_dir)
        for m in modified:
            print(f"  Modified: {m}")
        print(f"  Total: {len(modified)} files modified")
    else:
        print("  (kicadstamp/ not found, may already be renamed)")

    # Step 2: Rename package directory
    print("\n=== Step 2: Renaming kicadstamp/ -> kicadstamp/ ===")
    if rename_package_dir():
        print("  Done: kicadstamp/ -> kicadstamp/")
    else:
        print("  (already renamed or not found)")

    # Step 3: Rename root files
    print("\n=== Step 3: Renaming root files ===")
    for old, new in rename_root_files():
        print(f"  Renamed: {old} -> {new}")

    # Step 4: Replace content in the new kicadstamp/ directory
    print("\n=== Step 4: Replacing text in kicadstamp/ (confirm) ===")
    stamp_dir = os.path.join(BASE_DIR, "kicadstamp")
    if os.path.isdir(stamp_dir):
        modified = process_directory(stamp_dir)
        for m in modified:
            print(f"  Modified: {m}")
        print(f"  Total: {len(modified)} files modified (confirm pass)")

    # Step 5: Replace content in tests/
    print("\n=== Step 5: Replacing text in tests/ ===")
    test_dir = os.path.join(BASE_DIR, "tests")
    if os.path.isdir(test_dir):
        modified = process_directory(test_dir)
        for m in modified:
            print(f"  Modified: {m}")
        print(f"  Total: {len(modified)} files modified")

    # Step 6: Replace content in tools/
    print("\n=== Step 6: Replacing text in tools/ ===")
    tools_dir = os.path.join(BASE_DIR, "tools")
    if os.path.isdir(tools_dir):
        modified = process_directory(tools_dir)
        for m in modified:
            print(f"  Modified: {m}")
        print(f"  Total: {len(modified)} files modified")

    # Step 7: Replace content in diagnostics/ (top-level)
    print("\n=== Step 7: Replacing text in diagnostics/ (top-level) ===")
    diag_dir = os.path.join(BASE_DIR, "diagnostics")
    if os.path.isdir(diag_dir):
        modified = process_directory(diag_dir)
        for m in modified:
            print(f"  Modified: {m}")
        print(f"  Total: {len(modified)} files modified")

    # Step 8: Replace content in boards/
    print("\n=== Step 8: Replacing text in boards/ ===")
    boards_dir = os.path.join(BASE_DIR, "boards")
    if os.path.isdir(boards_dir):
        modified = process_directory(boards_dir)
        for m in modified:
            print(f"  Modified: {m}")
        print(f"  Total: {len(modified)} files modified")

    # Step 9: Replace content in docs/ and root .md files
    print("\n=== Step 9: Replacing text in docs/ and READMEs ===")
    md_files = []
    # docs/
    docs_dir = os.path.join(BASE_DIR, "docs")
    if os.path.isdir(docs_dir):
        md_files.extend(process_directory(docs_dir))
    # root READMEs
    for fn in ("README.md", "README_ru.md"):
        fp = os.path.join(BASE_DIR, fn)
        if os.path.isfile(fp) and replace_in_file(fp):
            md_files.append(fn)
    for m in md_files:
        print(f"  Modified: {m}")
    print(f"  Total: {len(md_files)} files modified")

    # Step 10: Replace content in YAML files (root level)
    print("\n=== Step 10: Replacing text in YAML files ===")
    yaml_modified = []
    for fn in os.listdir(BASE_DIR):
        if fn.endswith(".yaml") or fn.endswith(".yml"):
            fp = os.path.join(BASE_DIR, fn)
            if os.path.isfile(fp) and replace_in_file(fp):
                yaml_modified.append(fn)
    for m in yaml_modified:
        print(f"  Modified: {m}")

    # Step 11: Rename locale files
    print("\n=== Step 11: Renaming locale files ===")
    for rn in rename_locale_files():
        print(f"  Renamed: {rn}")

    # Step 12: Replace inside locale .po files (domain name references inside files)
    print("\n=== Step 12: Replacing domain name inside .po files ===")
    for lang_dir in os.listdir(LOCALES_DIR):
        lc_messages = os.path.join(LOCALES_DIR, lang_dir, "LC_MESSAGES")
        if not os.path.isdir(lc_messages):
            continue
        for fn in os.listdir(lc_messages):
            if fn.endswith(".po"):
                fp = os.path.join(lc_messages, fn)
                if replace_in_file(fp):
                    print(f"  Modified: {os.path.relpath(fp, BASE_DIR)}")

    # Step 13: Replace in techdocs/
    print("\n=== Step 13: Replacing text in techdocs/ ===")
    techdocs_dir = os.path.join(BASE_DIR, "techdocs")
    if os.path.isdir(techdocs_dir):
        modified = process_directory(techdocs_dir)
        for m in modified:
            print(f"  Modified: {m}")
        print(f"  Total: {len(modified)} files modified")

    print("\n=== Done! ===")


if __name__ == "__main__":
    main()
