#!/usr/bin/env python3
"""
link_cleanup — remove phantom wikilinks from "## Related" sections.

Ported from autograph (smixs/autograph), made self-contained for this fork:
no schema, no graph.json, stdlib only. Complements vault-health/fix_links.py
(which repairs broken links across the whole body via the graph). This one is
narrow and safe: it ONLY touches the "## Related" section and ONLY removes
links whose target resolves to no real note — the unfilled template placeholders
([[Previous related thought]], [[Project this belongs to]], ...) that pollute
hundreds of notes. Body links are never modified.

Resolution is conservative and case-insensitive: a link is kept if its target
matches ANY note's stem or relative path. So a link is removed only when it is
unambiguously phantom — the tool never drops a link that points at a real file.
If every link in a "## Related" section is phantom, the whole section is dropped.

Usage:
    link_cleanup.py <vault-dir>            # dry run — report only, writes nothing
    link_cleanup.py <vault-dir> --apply    # apply the removals
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

IGNORE_DIRS = {
    ".git", ".obsidian", ".session", ".graph", ".trash",
    "node_modules", ".claude", "templates",
}

WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]")
RELATED_RE = re.compile(r"(\n## Related\n)(.*?)(?=\n## |\Z)", re.DOTALL)


def walk_vault(vault_dir: Path) -> list[Path]:
    out = []
    for md in sorted(vault_dir.rglob("*.md")):
        if set(md.relative_to(vault_dir).parts) & IGNORE_DIRS:
            continue
        out.append(md)
    return out


def normalize_target(target: str) -> str:
    """File part of a wikilink target: drop #anchor, strip, lowercase."""
    target = target.strip()
    if "#" in target:
        target = target.split("#")[0].strip()
    return target.lower()


def build_valid_targets(files: list[Path], vault_dir: Path) -> set[str]:
    """Case-insensitive set of every resolvable link target (stems + paths)."""
    valid: set[str] = set()
    for md in files:
        rp = str(md.relative_to(vault_dir)).replace("\\", "/")
        rp_noext = rp[:-3] if rp.endswith(".md") else rp
        valid.add(rp_noext.lower())
        valid.add(md.stem.lower())
        # also index path suffixes so "folder/note" and "note" both resolve
        parts = rp_noext.split("/")
        for i in range(1, len(parts)):
            valid.add("/".join(parts[i:]).lower())
    return valid


def extract_targets(text: str) -> list[str]:
    return [m.group(1) for m in WIKILINK_RE.finditer(text)]


def cleanup_related_section(content: str, valid: set[str]) -> tuple[str, list[str], list[str]]:
    """Return (new_content, removed_targets, kept_targets). Only ## Related."""
    match = RELATED_RE.search(content)
    if not match:
        return content, [], []

    header, body = match.group(1), match.group(2)
    removed, kept, new_lines = [], [], []

    for line in body.split("\n"):
        targets = extract_targets(line)
        if not targets:
            new_lines.append(line)  # blank line / prose — keep
            continue
        line_has_valid = False
        for t in targets:
            norm = normalize_target(t)
            if not norm or norm in valid:
                kept.append(t)
                line_has_valid = True
            else:
                removed.append(t)
        if line_has_valid:
            new_lines.append(line)  # keep the line if any link survives

    if not removed:
        return content, [], kept

    if not kept:
        # every link phantom → drop the whole section
        new_content = content[:match.start()] + content[match.end():]
        return new_content.rstrip() + "\n", removed, kept

    new_section = header + "\n".join(new_lines)
    new_content = content[:match.start()] + new_section + content[match.end():]
    return new_content, removed, kept


def run_cleanup(vault_dir: Path, apply: bool = False) -> dict:
    vault_dir = vault_dir.resolve()
    files = walk_vault(vault_dir)
    valid = build_valid_targets(files, vault_dir)

    report = {
        "timestamp": datetime.now().isoformat(),
        "apply": apply,
        "files_scanned": len(files),
        "files_with_related": 0,
        "files_modified": 0,
        "links_removed_total": 0,
        "links_kept_total": 0,
        "removed": [],
    }

    for md in files:
        content = md.read_text(encoding="utf-8", errors="replace")
        if "## Related" not in content:
            continue
        report["files_with_related"] += 1
        new_content, removed, kept = cleanup_related_section(content, valid)
        report["links_kept_total"] += len(kept)
        if removed:
            report["files_modified"] += 1
            report["links_removed_total"] += len(removed)
            report["removed"].append({
                "file": str(md.relative_to(vault_dir)).replace("\\", "/"),
                "removed": removed,
            })
            if apply:
                md.write_text(new_content, encoding="utf-8")

    print(f"{'APPLY' if apply else 'DRY RUN'} link_cleanup:")
    print(f"  scanned: {report['files_scanned']}, with ## Related: {report['files_with_related']}")
    print(f"  files {'fixed' if apply else 'to fix'}: {report['files_modified']}")
    print(f"  phantom links removed: {report['links_removed_total']}, kept: {report['links_kept_total']}")
    if report["removed"] and not apply:
        print("  sample (first 10):")
        for item in report["removed"][:10]:
            print(f"    {item['file']}: " + ", ".join(f"[[{t}]]" for t in item["removed"][:4]))

    report_dir = vault_dir / ".graph"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "link-cleanup-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("Usage: link_cleanup.py <vault-dir> [--apply]", file=sys.stderr)
        sys.exit(1)
    vault_dir = Path(args[0])
    if not vault_dir.is_dir():
        print(f"Not a directory: {vault_dir}", file=sys.stderr)
        sys.exit(1)
    run_cleanup(vault_dir, apply="--apply" in args)


if __name__ == "__main__":
    main()
