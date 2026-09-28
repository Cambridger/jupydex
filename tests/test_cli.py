from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from jupydex.cli import _configure, _run, build_parser, main
from jupydex.client import CommandResult, ProxySupportError, RemoteOutcomeUnknownError
from jupydex.config import (
    ConfigurationError,
    Settings,
    load_config_file,
    save_config_file,
)


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "url": None,
        "auth": "token",
        "terminal": "agent_shell",
        "cwd": "/workspace/project",
        "origin": None,
        "saved_proxy": "auto",
        "ca_bundle": None,
        "no_verify_tls": False,
        "config": None,
        "show_config": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ConfigureTests(unittest.TestCase):
    def test_token_rotation_preserves_connection_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            old = {
                "url": "https://example.test", "token": "old",
                "terminal": "dedicated", "cwd": "/workspace/project",
                "proxy_mode": "none", "verify_tls": False, "request_timeout": 45,
                "origin": "https://origin.example",
            }
            save_config_file(path, old)
            args = build_parser().parse_args(["--config", str(path), "configure", "--url", old["url"]])
            with mock.patch("jupydex.cli.getpass.getpass", return_value="new"):
                _configure(args)
            self.assertEqual(load_config_file(path), dict(old, token="new"))

    def test_server_change_does_not_copy_private_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            save_config_file(path, {"url": "https://old.example", "token": "old", "terminal": "old", "cwd": "/private", "verify_tls": False})
            args = build_parser().parse_args(["configure", "--config", str(path), "--url", "https://new.example", "--auth", "none"])
            _configure(args)
            self.assertEqual(load_config_file(path), {"url": "https://new.example", "verify_tls": True, "proxy_mode": "auto"})

    def test_global_config_selects_file_for_read_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            save_config_file(path, {"url": "https://example.test"})
            with mock.patch.dict("os.environ", {}, clear=True), mock.patch("jupydex.cli._run", new_callable=mock.AsyncMock, return_value=[]) as run:
                self.assertEqual(main(["--config", str(path), "list"]), 0)
            self.assertEqual(run.call_args.args[1].base_url, "https://example.test")

    def test_proxy_override_is_applied_to_one_call(self) -> None:
        captured: dict[str, str] = {}

        async def capture(_: object, settings: Settings) -> list[object]:
            captured["proxy_mode"] = settings.proxy_mode
            return []

        with (
            mock.patch(
                "jupydex.cli.Settings.from_env",
                return_value=Settings(base_url="https://example.test"),
            ),
            mock.patch("jupydex.cli._run", new=capture),
        ):
            exit_code = main(["--proxy", "none", "list"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["proxy_mode"], "none")

    def test_proxy_support_error_is_structured_and_redacted(self) -> None:
        async def fail(*_: object, **__: object) -> object:
            raise ProxySupportError("socks_from_environment")

        stderr = io.StringIO()
        with (
            mock.patch(
                "jupydex.cli.Settings.from_env",
                return_value=Settings(base_url="https://example.test"),
            ),
            mock.patch("jupydex.cli._run", new=fail),
            redirect_stderr(stderr),
        ):
            exit_code = main(["list"])

        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["proxy_mode"], "socks_from_environment")
        self.assertTrue(payload["remediation"])
        self.assertNotIn("example.test", repr(payload))

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

    def test_configure_saves_proxy_but_never_prints_its_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "private.json"
            proxy = "socks5://user:secret@proxy.example:1080"
            with mock.patch(
                "jupydex.cli.getpass.getpass",
                return_value="test-only",
            ):
                result = _configure(
                    _args(
                        url="https://jupyter.example",
                        config=str(config_path),
                        saved_proxy=proxy,
                        show_config=True,
                    )
                )
            self.assertEqual(load_config_file(config_path)["proxy_mode"], proxy)
            self.assertEqual(result["config"]["proxy_mode"], "explicit_socks")
            self.assertNotIn("proxy.example", repr(result))
            self.assertNotIn("secret", repr(result))


class ScriptInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_and_stdin_preserve_shell_literals(self) -> None:
        script = "value='$HOME $(false)'\nprintf '%s\\n' \"$value\"\n"
        settings = Settings(base_url="https://example.test", terminal="agent")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "script.sh"
            path.write_text(script, encoding="utf-8")
            for source in (str(path), "-"):
                with self.subTest(source=source), mock.patch("jupydex.cli.JupyterTerminalClient") as constructor, mock.patch("jupydex.cli.sys.stdin", io.StringIO(script)):
                    client = constructor.return_value.__aenter__.return_value
                    client.execute.return_value = CommandResult("agent", script, "", 0, False, 0)
                    await _run(build_parser().parse_args(["exec", "--file", source]), settings)
                    self.assertEqual(client.execute.call_args.args[1], script)

    async def test_file_input_cannot_be_combined_with_other_commands(self) -> None:
        with mock.patch("jupydex.cli.JupyterTerminalClient"):
            for options in (["--shell", "true"], ["--", "true"]):
                args = build_parser().parse_args(["exec", "--file", "-", *options])
                with self.assertRaises(ConfigurationError):
                    await _run(args, Settings(base_url="https://example.test"))


if __name__ == "__main__":
    unittest.main()
