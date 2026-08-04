from __future__ import annotations

import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from jupydex.config import (
    ConfigurationError,
    Settings,
    load_config_file,
    normalize_proxy_mode,
    normalize_server_url,
    save_config_file,
)


class NormalizeUrlTests(unittest.TestCase):
    def test_proxy_modes_are_normalized_and_validated(self) -> None:
        self.assertEqual(normalize_proxy_mode(None), "auto")
        self.assertEqual(normalize_proxy_mode(" NONE "), "none")
        self.assertEqual(
            normalize_proxy_mode("SOCKS5://proxy.example:1080"),
            "socks5://proxy.example:1080",
        )
        with self.assertRaises(ConfigurationError):
            normalize_proxy_mode("proxy.example:1080")
        with self.assertRaises(ConfigurationError):
            normalize_proxy_mode("https://proxy.example/path")
        with self.assertRaises(ConfigurationError):
            normalize_proxy_mode("socks4://proxy.example:1080")

    def test_proxy_policy_maps_to_rest_and_websocket_transports(self) -> None:
        automatic = Settings(base_url="https://example.test")
        direct = Settings(base_url="https://example.test", proxy_mode="none")
        explicit = Settings(
            base_url="https://example.test",
            proxy_mode="socks5://user:secret@proxy.example:1080",
        )
        self.assertEqual(automatic.httpx_proxy_kwargs, {"trust_env": True})
        self.assertIs(automatic.websocket_proxy, True)
        self.assertEqual(direct.httpx_proxy_kwargs, {"trust_env": False})
        self.assertIsNone(direct.websocket_proxy)
        self.assertEqual(
            explicit.httpx_proxy_kwargs,
            {
                "proxy": "socks5://user:secret@proxy.example:1080",
                "trust_env": False,
            },
        )
        self.assertEqual(
            explicit.websocket_proxy,
            "socks5://user:secret@proxy.example:1080",
        )
        self.assertEqual(explicit.proxy_label, "explicit_socks")
        summary = repr(explicit.public_summary(reveal_sensitive=True))
        self.assertNotIn("proxy.example", summary)
        self.assertNotIn("secret", summary)

    def test_environment_proxy_label_is_redacted_and_honors_bypass(self) -> None:
        settings = Settings(base_url="https://jupyter.example")
        with (
            mock.patch("jupydex.config.proxy_bypass", return_value=False),
            mock.patch(
                "jupydex.config.getproxies",
                return_value={"all": "socks5://user:secret@proxy.example:1080"},
            ),
        ):
            self.assertEqual(
                settings.effective_websocket_proxy_label(),
                "socks_from_environment",
            )
        with mock.patch("jupydex.config.proxy_bypass", return_value=True):
            self.assertEqual(
                settings.effective_websocket_proxy_label(),
                "direct_from_environment",
            )

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
                    "proxy_mode": "auto",
                },
            )
            settings = Settings.from_env(
                {
                    "JUPYDEX_CONFIG": str(path),
                    "JUPYDEX_TERMINAL": "from-env",
                    "JUPYDEX_PROXY": "none",
                }
            )
            self.assertEqual(settings.base_url, "https://file.example")
            self.assertEqual(settings.token, "file-token")
            self.assertEqual(settings.terminal, "from-env")
            self.assertEqual(settings.proxy_mode, "none")
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

    def test_explicit_proxy_config_with_open_permissions_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "url": "https://example.test",
                        "proxy_mode": (
                            "socks5://user:secret@proxy.example:1080"
                        ),
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(path, 0o644)
            with self.assertRaises(ConfigurationError):
                load_config_file(path)


if __name__ == "__main__":
    unittest.main()
