from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jupydex.cli import _configure
from jupydex.config import ConfigurationError, load_config_file


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "url": None,
        "auth": "token",
        "terminal": "agent_shell",
        "cwd": "/workspace/project",
        "origin": None,
        "ca_bundle": None,
        "no_verify_tls": False,
        "config": None,
        "show_config": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ConfigureTests(unittest.TestCase):
    def test_token_in_url_argument_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            _configure(
                _args(
                    url=(
                        "https://jupyter.example/lab"
                        "?token=test-only"
                    )
                )
            )

    def test_interactive_token_url_is_saved_but_not_printed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "private.json"
            with mock.patch(
                "jupydex.cli.getpass.getpass",
                side_effect=[
                    (
                        "https://203.0.113.10/lab"
                        "?token=test-only"
                    ),
                    "",
                ],
            ):
                result = _configure(_args(config=str(config_path)))

            rendered = repr(result)
            self.assertNotIn("203.0.113.10", rendered)
            self.assertNotIn("test-only", rendered)
            self.assertEqual(result["config"]["base_url"], "https://<redacted>")
            saved = load_config_file(config_path)
            self.assertEqual(saved["token"], "test-only")
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

    def test_show_config_never_reveals_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "private.json"
            with mock.patch(
                "jupydex.cli.getpass.getpass",
                return_value="test-only",
            ):
                result = _configure(
                    _args(
                        url="https://jupyter.example",
                        config=str(config_path),
                        show_config=True,
                    )
                )
            rendered = repr(result)
            self.assertIn("https://jupyter.example", rendered)
            self.assertNotIn("test-only", rendered)


if __name__ == "__main__":
    unittest.main()
