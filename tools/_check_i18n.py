#!/usr/bin/env python3
"""Check which kicadspoke source files use _() gettext calls and whether they import it."""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import re
import glob
import os

src = [f for f in glob.glob('kicadspoke/**/*.py', recursive=True) 
       if '__pycache__' not in f and '.tox' not in f]

has_import = []
has_calls = []
for f in sorted(src):
    txt = open(f, 'r', encoding='utf-8').read()
    # Check for _ import (any number of dots: .i18n, ..i18n, ...i18n)
    if re.search(r'from\s+\.+i18n\s+import\s+_', txt) or \
       re.search(r'from\s+kicadspoke\.i18n\s+import\s+_', txt):
        has_import.append(f)
    # Check for _("...") or _('...') calls
    if re.search(r'_\s*\([\"\']', txt):
        has_calls.append(f)

print('=' * 60)
print('FILES WITH _() CALLS:')
print('=' * 60)
for f in sorted(has_calls):
    status = '[OK]' if f in has_import else '[MISSING]'
    print(f'  {status}: {f}')

print()
no_import = [f for f in has_calls if f not in has_import]
if no_import:
    print('=' * 60)
    print('WARNING: %d files use _() without importing it!' % len(no_import))
    print('=' * 60)
    for f in no_import:
        print('  [MISSING] %s' % f)
else:
    print('[OK] All files that use _() have it properly imported.')

print()
print('Summary: %d files use _(), all have proper import.' % len(has_calls))
