#!/usr/bin/env python3
"""Validate repository-relative links in Markdown and simple HTML attributes."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
HTML_LINK = re.compile(r"""(?:href|src)=["']([^"']+)["']""", re.IGNORECASE)
REMOTE_PREFIXES = ("http://", "https://", "mailto:")


def _targets(text: str):
    yield from MARKDOWN_LINK.findall(text)
    yield from HTML_LINK.findall(text)


def _local_path(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith(REMOTE_PREFIXES) or target.startswith("#"):
        return None
    target = target.split("#", 1)[0]
    if not target:
        return None
    return (source.parent / unquote(target)).resolve()


def check(root: Path) -> list[tuple[Path, str]]:
    root = root.resolve()
    failures: list[tuple[Path, str]] = []
    for source in sorted(root.rglob("*.md")):
        if any(part in {".git", ".venv", "build", "dist"} for part in source.parts):
            continue
        text = source.read_text(encoding="utf-8")
        for raw_target in _targets(text):
            local = _local_path(source, raw_target)
            if local is None:
                continue
            try:
                local.relative_to(root)
            except ValueError:
                failures.append((source.relative_to(root), raw_target))
                continue
            if not local.exists():
                failures.append((source.relative_to(root), raw_target))
    return failures


def main() -> int:
    failures = check(ROOT)
    if failures:
        print("Relative link check failed:", file=sys.stderr)
        for source, target in failures:
            print(f"- {source}: {target}", file=sys.stderr)
        return 1
    print("Relative link check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
