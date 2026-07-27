from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from jupydex.cli import _configure, build_parser, main
from jupydex.client import RemoteOutcomeUnknownError
from jupydex.config import (
    ConfigurationError,
    Settings,
    load_config_file,
)


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
    def test_unknown_remote_outcome_is_structured(self) -> None:
        async def fail(*_: object, **__: object) -> object:
            raise RemoteOutcomeUnknownError(
                "agent_shell",
                reconnect_attempts=3,
                operation_id="deploy_123",
            )

        stderr = io.StringIO()
        with (
            mock.patch(
                "jupydex.cli.Settings.from_env",
                return_value=Settings(base_url="https://example.test"),
            ),
            mock.patch("jupydex.cli._run", new=fail),
            redirect_stderr(stderr),
        ):
            exit_code = main(
                ["exec", "--terminal", "agent_shell", "--", "true"]
            )

        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["remote_outcome"], "unknown")
        self.assertTrue(payload["terminal_retained"])
        self.assertEqual(payload["reconnect_attempts"], 3)
        self.assertEqual(payload["operation_id"], "deploy_123")

    def test_operation_subcommands_parse_recovery_fields(self) -> None:
        args = build_parser().parse_args(
            [
                "operation",
                "--terminal",
                "agent_shell",
                "set",
                "--directory",
                "/workspace/project/logs/jupydex_ops",
                "--id",
                "deploy_123",
                "--state",
                "TERM_SENT",
            ]
        )
        self.assertEqual(args.action, "operation")
        self.assertEqual(args.operation_action, "set")
        self.assertEqual(args.operation_id, "deploy_123")
        self.assertEqual(args.state, "TERM_SENT")

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
