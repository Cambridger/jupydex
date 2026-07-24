#!/usr/bin/env python3
"""Fail when a release tree appears to contain private connection material."""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
SKIP_FILES = {Path("tools/check_release.py")}
ALLOWED_FILE_NAMES = {".env.example"}
FORBIDDEN_FILE_NAMES = {
    ".env",
    ".netrc",
    ".pypirc",
    "config.json",
    "credentials.json",
}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".ini",
    ".in",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".svg",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
PATTERNS = {
    "macOS user-specific absolute path": re.compile(
        rb"/Users/[A-Za-z0-9._-]+/"
    ),
    "Linux user-specific absolute path": re.compile(
        rb"/home/[A-Za-z0-9._-]+/"
    ),
    "mounted absolute project path": re.compile(rb"/mnt/[A-Za-z0-9._/-]+"),
    "token-bearing URL": re.compile(
        rb"https?://[^\s\"'<>]+[?&](?:token|auth|key)=[^\s\"'<>]{16,}",
        re.IGNORECASE,
    ),
    "long hexadecimal secret-like literal": re.compile(
        rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{48,}(?![0-9A-Fa-f])"
    ),
    "private key block": re.compile(rb"BEGIN [A-Z ]*PRIVATE KEY"),
    "long credential assignment": re.compile(
        rb"\b(?:token|password|cookie|secret)\s*[:=]\s*[\"'][^\"'\r\n]{32,}",
        re.IGNORECASE,
    ),
}
IPV4 = re.compile(
    rb"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])"
)


def _iter_release_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if relative in SKIP_FILES:
            continue
        yield path, relative


def _is_allowed_documentation_ip(address: ipaddress.IPv4Address) -> bool:
    documentation_networks = (
        ipaddress.ip_network("192.0.2.0/24"),
        ipaddress.ip_network("198.51.100.0/24"),
        ipaddress.ip_network("203.0.113.0/24"),
    )
    return address.is_loopback or any(
        address in network for network in documentation_networks
    )


def scan(root: Path) -> list[tuple[Path, str]]:
    findings: list[tuple[Path, str]] = []
    for path, relative in _iter_release_files(root):
        if (
            path.name in FORBIDDEN_FILE_NAMES
            and path.name not in ALLOWED_FILE_NAMES
        ):
            findings.append((relative, "credential-like filename"))
        if path.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}:
            findings.append((relative, "private key or certificate filename"))
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        data = path.read_bytes()
        for label, pattern in PATTERNS.items():
            if pattern.search(data):
                findings.append((relative, label))
        for raw_match in IPV4.findall(data):
            try:
                address = ipaddress.ip_address(raw_match.decode("ascii"))
            except ValueError:
                continue
            if not _is_allowed_documentation_ip(address):
                findings.append((relative, "non-documentation IPv4 address"))
                break
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="scan a Jupydex release tree for likely private material"
    )
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=ROOT,
        help="tree to scan; defaults to the repository root",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    findings = scan(root)
    if findings:
        print("Release privacy check failed:", file=sys.stderr)
        for path, label in findings:
            print(f"- {path}: {label}", file=sys.stderr)
        return 1
    print("Release privacy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
