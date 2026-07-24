from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_links import check
from tools.check_release import scan


class ReleaseCheckTests(unittest.TestCase):
    def test_live_public_ip_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "Synthetic mistake: https://" + "8.8." + "8.8/lab\n",
                encoding="utf-8",
            )
            findings = scan(root)
            self.assertIn(
                (Path("README.md"), "non-documentation IPv4 address"),
                findings,
            )

    def test_reserved_documentation_ip_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "Example: https://203.0.113.10/lab\n",
                encoding="utf-8",
            )
            self.assertEqual(scan(root), [])

    def test_credential_filename_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("EXAMPLE=value\n", encoding="utf-8")
            self.assertIn(
                (Path(".env"), "credential-like filename"),
                scan(root),
            )


class LinkCheckTests(unittest.TestCase):
    def test_existing_relative_link_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "guide.md").write_text("# Guide\n", encoding="utf-8")
            (root / "README.md").write_text(
                "[Guide](guide.md#guide)\n",
                encoding="utf-8",
            )
            self.assertEqual(check(root), [])

    def test_missing_relative_link_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "[Missing](docs/missing.md)\n",
                encoding="utf-8",
            )
            self.assertEqual(
                check(root),
                [(Path("README.md"), "docs/missing.md")],
            )


if __name__ == "__main__":
    unittest.main()
