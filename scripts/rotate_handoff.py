#!/usr/bin/env python3
"""Trim handoff.md: keep only frontmatter + current ## Last Session + first ## Observations.

Removes all '## Last Session (предыдущая...)' rolling sections, which Phase 3 keeps
appending without ever cleaning up. Without this, handoff grows ~3 KB/day and bloats
the REFLECT prompt context.
"""
import re
import sys
from pathlib import Path

PATH = Path('vault/.session/handoff.md')
if not PATH.exists():
    sys.exit(0)

text = PATH.read_text(encoding='utf-8')

# Drop everything from the first 'предыдущая' Last Session onward
m = re.search(r'\n## Last Session \(', text)
if m:
    text = text[:m.start()].rstrip() + '\n'

# If somehow two Observations sections survived — keep only the first
parts = re.split(r'(?m)^## Observations\b', text)
if len(parts) > 2:
    text = parts[0] + '## Observations' + parts[1]
    text = text.rstrip() + '\n'

PATH.write_text(text, encoding='utf-8')
print(f'rotate_handoff: kept {len(text)} bytes')
