from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.request import getproxies, proxy_bypass


class ConfigurationError(ValueError):
    """Raised when gateway configuration is missing or invalid."""


_PROXY_SCHEMES = {
    "http",
    "https",
    "socks5",
    "socks5h",
}


def _as_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"invalid boolean value: {value!r}")


def normalize_server_url(raw_url: str) -> tuple[str, str | None]:
    """Return a Jupyter Server base URL and an optional token from its query."""
    value = raw_url.strip()
    if not value:
        raise ConfigurationError("JUPYDEX_URL is empty")
    if "://" not in value:
        value = f"http://{value}"

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(
            "JUPYDEX_URL must be an http(s) Jupyter Server URL"
        )
    if parsed.username or parsed.password:
        raise ConfigurationError(
            "do not embed a username or password in JUPYDEX_URL; use the "
            "dedicated token or cookie environment variable"
        )

    path = parsed.path.rstrip("/")
    # Accept a URL copied from JupyterLab. For JupyterHub this preserves
    # prefixes such as /user/alice and removes only the UI portion.
    for marker in ("/lab/", "/lab", "/tree/", "/tree"):
        index = path.find(marker)
        if index >= 0:
            path = path[:index]
            break

    query = parse_qs(parsed.query)
    query_token = query.get("token", [None])[0]
    base_url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return base_url.rstrip("/"), query_token


def normalize_proxy_mode(raw_proxy: str | None) -> str:
    """Return ``auto``, ``none``, or a validated explicit proxy URL."""
    value = "auto" if raw_proxy is None else raw_proxy.strip()
    normalized = value.lower()
    if normalized in {"auto", "none"}:
        return normalized
    if not value:
        raise ConfigurationError(
            "proxy mode must be 'auto', 'none', or an explicit proxy URL"
        )

    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in _PROXY_SCHEMES or not parsed.hostname:
        raise ConfigurationError(
            "proxy URL must use http, https, socks5, or socks5h and include "
            "a host"
        )
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ConfigurationError(
            "proxy URL must not contain a path, query string, or fragment"
        )
    try:
        parsed.port
    except ValueError as exc:
        raise ConfigurationError("proxy URL contains an invalid port") from exc
    return urlunsplit((scheme, parsed.netloc, "", "", ""))


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str
    token: str | None = None
    cookie: str | None = None
    origin: str | None = None
    verify_tls: bool = True
    ca_bundle: Path | None = None
    request_timeout: float = 20.0
    terminal: str | None = None
    cwd: str | None = None
    proxy_mode: str = "auto"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        config = _load_selected_config(source, use_default=env is None)
        raw_url = (
            source.get("JUPYDEX_URL")
            or source.get("JUPYTER_URL")
            or _config_string(config, "url")
            or _config_string(config, "base_url")
        )
        if not raw_url:
            raise ConfigurationError(
                "set JUPYDEX_URL or run `jdx configure`"
            )
        base_url, query_token = normalize_server_url(raw_url)
        token = (
            source.get("JUPYDEX_TOKEN")
            or source.get("JUPYTER_TOKEN")
            or _config_string(config, "token")
            or query_token
        )
        cookie = source.get("JUPYDEX_COOKIE") or _config_string(config, "cookie")
        ca_value = source.get("JUPYDEX_CA_BUNDLE") or _config_string(
            config, "ca_bundle"
        )
        try:
            request_timeout = float(
                source.get("JUPYDEX_TIMEOUT")
                or config.get("request_timeout")
                or "20"
            )
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("JUPYDEX_TIMEOUT must be a number") from exc
        if request_timeout <= 0:
            raise ConfigurationError("JUPYDEX_TIMEOUT must be positive")
        proxy_value = source.get("JUPYDEX_PROXY")
        if proxy_value is None:
            proxy_value = _config_string(config, "proxy_mode")
        return cls(
            base_url=base_url,
            token=token,
            cookie=cookie,
            origin=source.get("JUPYDEX_ORIGIN")
            or _config_string(config, "origin"),
            verify_tls=_as_bool(
                source.get("JUPYDEX_VERIFY_TLS", config.get("verify_tls")),
                True,
            ),
            ca_bundle=Path(ca_value).expanduser() if ca_value else None,
            request_timeout=request_timeout,
            terminal=source.get("JUPYDEX_TERMINAL")
            or _config_string(config, "terminal"),
            cwd=source.get("JUPYDEX_CWD") or _config_string(config, "cwd"),
            proxy_mode=normalize_proxy_mode(proxy_value),
        )

    @property
    def http_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "jupydex/0.4"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        if self.cookie:
            headers["Cookie"] = self.cookie
            xsrf = _cookie_value(self.cookie, "_xsrf")
            if xsrf:
                headers["X-XSRFToken"] = xsrf
        return headers

    @property
    def websocket_url_prefix(self) -> str:
        parsed = urlsplit(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunsplit((scheme, parsed.netloc, parsed.path, "", "")).rstrip("/")

    @property
    def websocket_origin(self) -> str:
        if self.origin:
            return self.origin
        parsed = urlsplit(self.base_url)
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    @property
    def proxy_kind(self) -> str:
        mode = normalize_proxy_mode(self.proxy_mode)
        if mode in {"auto", "none"}:
            return mode
        return "socks" if urlsplit(mode).scheme.startswith("socks") else "http"

    @property
    def proxy_label(self) -> str:
        kind = self.proxy_kind
        return f"explicit_{kind}" if kind in {"http", "socks"} else kind

    @property
    def httpx_proxy_kwargs(self) -> dict[str, object]:
        mode = normalize_proxy_mode(self.proxy_mode)
        if mode == "auto":
            return {"trust_env": True}
        if mode == "none":
            return {"trust_env": False}
        return {"proxy": mode, "trust_env": False}

    @property
    def websocket_proxy(self) -> bool | str | None:
        mode = normalize_proxy_mode(self.proxy_mode)
        if mode == "auto":
            return True
        if mode == "none":
            return None
        return mode

    def effective_websocket_proxy_label(self) -> str:
        """Describe proxy selection without returning an address or credentials."""
        mode = normalize_proxy_mode(self.proxy_mode)
        if mode != "auto":
            return self.proxy_label
        parsed = urlsplit(self.base_url)
        host = parsed.hostname or ""
        if host and proxy_bypass(host):
            return "direct_from_environment"
        proxies = {key.lower(): value for key, value in getproxies().items()}
        websocket_scheme = "wss" if parsed.scheme == "https" else "ws"
        keys = [websocket_scheme, "socks"]
        keys.append("https" if websocket_scheme == "wss" else "http")
        keys.append("all")
        selected = next(
            (proxies[key] for key in keys if proxies.get(key)),
            None,
        )
        if not selected:
            return "direct_from_environment"
        proxy_scheme = urlsplit(selected).scheme.lower()
        kind = "socks" if proxy_scheme.startswith("socks") else "http"
        return f"{kind}_from_environment"

    def ssl_context(self) -> ssl.SSLContext | None:
        if not self.base_url.startswith("https://"):
            return None
        context = ssl.create_default_context(
            cafile=str(self.ca_bundle) if self.ca_bundle else None
        )
        if not self.verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def public_summary(self, *, reveal_sensitive: bool = False) -> dict[str, object]:
        """Return a credential-free summary, with endpoints hidden by default."""
        secure_transport = self.base_url.startswith("https://")
        warnings: list[str] = []
        if not secure_transport:
            warnings.append(
                "HTTP/WS is unencrypted; use HTTPS/WSS, a VPN, or an SSH "
                "port-forward before treating this channel like SSH."
            )
        if secure_transport and not self.verify_tls:
            warnings.append(
                "TLS certificate verification is disabled; this permits "
                "machine-in-the-middle attacks."
            )
        return {
            "base_url": (
                self.base_url
                if reveal_sensitive
                else f"{urlsplit(self.base_url).scheme}://<redacted>"
            ),
            "authentication": (
                "token" if self.token else "cookie" if self.cookie else "none"
            ),
            "transport_security": "TLS" if secure_transport else "PLAINTEXT",
            "verify_tls": self.verify_tls,
            "ca_bundle": (
                str(self.ca_bundle)
                if reveal_sensitive and self.ca_bundle
                else "<configured>"
                if self.ca_bundle
                else None
            ),
            "request_timeout": self.request_timeout,
            "proxy_mode": self.proxy_label,
            "terminal": (
                self.terminal
                if reveal_sensitive
                else "<configured>"
                if self.terminal
                else None
            ),
            "cwd": (
                self.cwd
                if reveal_sensitive
                else "<configured>"
                if self.cwd
                else None
            ),
            "warnings": warnings,
        }


def _cookie_value(cookie_header: str, name: str) -> str | None:
    for item in cookie_header.split(";"):
        key, separator, value = item.strip().partition("=")
        if separator and key == name:
            return value
    return None


def default_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "jupydex" / "config.json"


def load_config_file(path: Path) -> dict[str, Any]:
    expanded = path.expanduser()
    try:
        stat = expanded.stat()
    except FileNotFoundError:
        return {}
    try:
        payload = json.loads(expanded.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read Jupydex config {expanded}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError(f"Jupydex config must contain a JSON object: {expanded}")
    if _config_contains_sensitive_data(payload) and stat.st_mode & 0o077:
        raise ConfigurationError(
            "Jupydex config contains credentials or an explicit proxy URL but "
            f"is not private: {expanded}; run `chmod 600 {expanded}`"
        )
    return payload


def save_config_file(path: Path, payload: Mapping[str, Any]) -> None:
    expanded = path.expanduser()
    expanded.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = expanded.with_name(f".{expanded.name}.{os.getpid()}.tmp")
    serialized = json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, expanded)
        os.chmod(expanded, 0o600)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _load_selected_config(
    source: Mapping[str, str], *, use_default: bool
) -> dict[str, Any]:
    selected = source.get("JUPYDEX_CONFIG")
    if selected == "":
        return {}
    if selected:
        return load_config_file(Path(selected))
    if use_default:
        return load_config_file(default_config_path())
    return {}


def _config_string(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"Jupydex config field {key!r} must be a string")
    return value


def _config_contains_sensitive_data(payload: Mapping[str, Any]) -> bool:
    if payload.get("token") or payload.get("cookie"):
        return True
    proxy = payload.get("proxy_mode")
    return isinstance(proxy, str) and proxy.strip().lower() not in {
        "",
        "auto",
        "none",
    }
