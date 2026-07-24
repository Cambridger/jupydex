from __future__ import annotations

import unittest
import json
import os
import tempfile
from pathlib import Path

from jupydex.config import (
    ConfigurationError,
    Settings,
    load_config_file,
    normalize_server_url,
    save_config_file,
)


class NormalizeUrlTests(unittest.TestCase):
    def test_copied_lab_url_is_reduced_to_server_base(self) -> None:
        base, token = normalize_server_url(
            "https://example.test/user/alice/lab/workspaces/auto-W/tree/Js_rl/eabo"
            "?token=secret"
        )
        self.assertEqual(base, "https://example.test/user/alice")
        self.assertEqual(token, "secret")

    def test_plain_host_gets_http_scheme(self) -> None:
        base, token = normalize_server_url("localhost:8888/")
        self.assertEqual(base, "http://localhost:8888")
        self.assertIsNone(token)

    def test_invalid_boolean_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_env(
                {"JUPYDEX_URL": "https://example.test", "JUPYDEX_VERIFY_TLS": "maybe"}
            )

    def test_embedded_basic_auth_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            normalize_server_url("https://alice:secret@example.test/lab")

    def test_secret_is_not_in_public_summary(self) -> None:
        settings = Settings.from_env(
            {
                "JUPYDEX_URL": "https://example.test/?token=url-secret",
                "JUPYDEX_TOKEN": "env-secret",
                "JUPYDEX_COOKIE": "_xsrf=abc; session=private",
            }
        )
        rendered = repr(settings.public_summary())
        self.assertNotIn("secret", rendered)
        self.assertNotIn("private", rendered)
        self.assertEqual(settings.public_summary()["authentication"], "token")
        self.assertEqual(
            settings.public_summary()["base_url"], "https://<redacted>"
        )

    def test_sensitive_config_details_require_explicit_reveal(self) -> None:
        settings = Settings(
            base_url="https://jupyter.example/user/alice",
            terminal="agent_shell",
            cwd="/srv/project",
            ca_bundle=Path("/certs/team-ca.pem"),
        )
        hidden = settings.public_summary()
        visible = settings.public_summary(reveal_sensitive=True)
        self.assertNotIn("alice", repr(hidden))
        self.assertNotIn("/srv/project", repr(hidden))
        self.assertEqual(visible["terminal"], "agent_shell")
        self.assertEqual(visible["cwd"], "/srv/project")

    def test_disabled_tls_verification_is_reported(self) -> None:
        settings = Settings(
            base_url="https://jupyter.example", verify_tls=False
        )
        self.assertTrue(settings.public_summary()["warnings"])

    def test_xsrf_cookie_is_forwarded(self) -> None:
        settings = Settings(
            base_url="https://example.test",
            cookie="_xsrf=abc123; session=xyz",
        )
        self.assertEqual(settings.http_headers["X-XSRFToken"], "abc123")

    def test_private_config_is_loaded_and_env_overrides_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config_file(
                path,
                {
                    "url": "https://file.example",
                    "token": "file-token",
                    "terminal": "from-file",
                },
            )
            settings = Settings.from_env(
                {
                    "JUPYDEX_CONFIG": str(path),
                    "JUPYDEX_TERMINAL": "from-env",
                }
            )
            self.assertEqual(settings.base_url, "https://file.example")
            self.assertEqual(settings.token, "file-token")
            self.assertEqual(settings.terminal, "from-env")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_credential_config_with_open_permissions_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"url": "https://example.test", "token": "secret"}),
                encoding="utf-8",
            )
            os.chmod(path, 0o644)
            with self.assertRaises(ConfigurationError):
                load_config_file(path)


if __name__ == "__main__":
    unittest.main()
