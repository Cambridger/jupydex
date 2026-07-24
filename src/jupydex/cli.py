from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .client import GatewayError, JupyterTerminalClient
from .config import (
    ConfigurationError,
    Settings,
    default_config_path,
    normalize_server_url,
    save_config_file,
)


KEYS = {
    "ctrl-c": "\x03",
    "ctrl-d": "\x04",
    "enter": "\r",
    "escape": "\x1b",
    "tab": "\t",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jdx",
        description="Control a dedicated JupyterLab terminal without browser automation.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="pretty-print JSON instead of compact JSON",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    configure = subparsers.add_parser(
        "configure", help="save a private connection config for future jdx calls"
    )
    configure.add_argument(
        "--url",
        help=(
            "JupyterLab or server URL without credentials; omit to enter a "
            "token-bearing URL with hidden input"
        ),
    )
    configure.add_argument(
        "--auth",
        choices=("token", "cookie", "none"),
        default="token",
        help="credential type to prompt for",
    )
    configure.add_argument("--terminal", help="dedicated terminal name")
    configure.add_argument("--cwd", help="default remote working directory")
    configure.add_argument("--origin", help="override WebSocket Origin")
    configure.add_argument("--ca-bundle", help="private CA certificate bundle")
    configure.add_argument(
        "--no-verify-tls",
        action="store_true",
        help="disable TLS verification (not recommended)",
    )
    configure.add_argument(
        "--config",
        help="config path; defaults to ~/.config/jupydex/config.json",
    )
    configure.add_argument(
        "--show-config",
        action="store_true",
        help="include endpoint and remote paths in the JSON result",
    )

    doctor = subparsers.add_parser(
        "doctor", help="check API access and show a redacted config summary"
    )
    doctor.add_argument(
        "--show-config",
        action="store_true",
        help="include endpoint, terminal name, and remote paths in output",
    )
    subparsers.add_parser("list", help="list Jupyter terminal sessions")

    shell = subparsers.add_parser(
        "shell", help="open an SSH-like interactive Jupyter terminal"
    )
    shell.add_argument(
        "--terminal", help="terminal name; defaults to JUPYDEX_TERMINAL"
    )
    shell.epilog = "Press Ctrl-] to detach without stopping the remote shell."

    create = subparsers.add_parser("create", help="create a dedicated terminal")
    create.add_argument("--name", help="requested terminal name")
    create.add_argument("--cwd", help="change the new terminal to this directory")

    execute = subparsers.add_parser("exec", help="run a command and capture its exit code")
    execute.add_argument("--terminal", help="terminal name; defaults to JUPYDEX_TERMINAL")
    execute.add_argument("--cwd", help="change directory before running the command")
    execute.add_argument("--timeout", type=float, default=60.0)
    execute.add_argument("--max-chars", type=int, default=200_000)
    execute.add_argument("--raw", action="store_true", help="keep ANSI control codes")
    execute.add_argument(
        "--shell",
        dest="shell_command",
        help="run this exact shell string (useful for pipes and redirections)",
    )
    execute.add_argument(
        "--interrupt-on-timeout",
        action="store_true",
        help="send Ctrl-C if the wait timeout expires",
    )
    execute.add_argument(
        "--show-command",
        action="store_true",
        help="include the complete command in JSON output (may expose secrets)",
    )
    execute.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command after --, for example: jdx exec --terminal 1 -- pwd",
    )

    watch = subparsers.add_parser("watch", help="read recent/live terminal output")
    watch.add_argument("--terminal", help="terminal name; defaults to JUPYDEX_TERMINAL")
    watch.add_argument("--seconds", type=float, default=2.0)
    watch.add_argument("--max-chars", type=int, default=40_000)
    watch.add_argument("--raw", action="store_true")

    send = subparsers.add_parser("send", help="send text or a control key")
    send.add_argument("--terminal", help="terminal name; defaults to JUPYDEX_TERMINAL")
    input_group = send.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--text")
    input_group.add_argument("--key", choices=sorted(KEYS))
    send.add_argument("--enter", action="store_true", help="append Enter after --text")
    send.add_argument("--listen-seconds", type=float, default=0.5)
    send.add_argument("--raw", action="store_true")

    interrupt = subparsers.add_parser("interrupt", help="send Ctrl-C")
    interrupt.add_argument("--terminal", help="terminal name; defaults to JUPYDEX_TERMINAL")
    interrupt.add_argument("--listen-seconds", type=float, default=1.0)

    close = subparsers.add_parser("close", help="delete a Jupyter terminal")
    close.add_argument("--terminal", help="terminal name; defaults to JUPYDEX_TERMINAL")
    close.add_argument(
        "--yes",
        action="store_true",
        help="confirm deletion of this terminal",
    )
    return parser


def _terminal(args: argparse.Namespace, settings: Settings) -> str:
    value = getattr(args, "terminal", None) or settings.terminal
    if not value:
        raise ConfigurationError(
            "specify --terminal or set JUPYDEX_TERMINAL; Jupydex will not "
            "guess and take over an existing user terminal"
        )
    return value


async def _run(args: argparse.Namespace, settings: Settings) -> dict[str, Any] | list[Any]:
    async with JupyterTerminalClient(settings) as client:
        if args.action == "doctor":
            return await client.status(reveal_sensitive=args.show_config)
        if args.action == "list":
            return await client.list_terminals()
        if args.action == "shell":
            return await client.interactive_shell(_terminal(args, settings))
        if args.action == "create":
            return await client.create_terminal(name=args.name, cwd=args.cwd)
        if args.action == "exec":
            command_parts = list(args.command)
            if command_parts and command_parts[0] == "--":
                command_parts.pop(0)
            if args.shell_command is not None and command_parts:
                raise ConfigurationError(
                    "use either --shell or command arguments after --, not both"
                )
            command = (
                args.shell_command
                if args.shell_command is not None
                else shlex.join(command_parts)
            )
            result = await client.execute(
                _terminal(args, settings),
                command,
                cwd=args.cwd,
                timeout=args.timeout,
                max_chars=args.max_chars,
                raw=args.raw,
                interrupt_on_timeout=args.interrupt_on_timeout,
            )
            return result.as_dict(include_command=args.show_command)
        if args.action == "watch":
            return await client.watch(
                _terminal(args, settings),
                seconds=args.seconds,
                max_chars=args.max_chars,
                raw=args.raw,
            )
        if args.action == "send":
            text = KEYS[args.key] if args.key else args.text
            assert text is not None
            return await client.send(
                _terminal(args, settings),
                text,
                append_enter=args.enter,
                listen_seconds=args.listen_seconds,
                raw=args.raw,
            )
        if args.action == "interrupt":
            return await client.interrupt(
                _terminal(args, settings),
                listen_seconds=args.listen_seconds,
            )
        if args.action == "close":
            name = _terminal(args, settings)
            if not args.yes:
                raise ConfigurationError(
                    f"refusing to delete terminal {name!r} without --yes"
                )
            return await client.delete_terminal(name)
    raise AssertionError(f"unhandled action: {args.action}")


def _configure(args: argparse.Namespace) -> dict[str, Any]:
    raw_url = args.url
    if raw_url is None:
        raw_url = getpass.getpass(
            "JupyterLab URL (input hidden because it may contain a token): "
        ).strip()
        if not raw_url:
            raise ConfigurationError("no JupyterLab URL was provided")
    base_url, url_token = normalize_server_url(raw_url)
    if args.url is not None and url_token:
        raise ConfigurationError(
            "refusing a token in --url because command-line arguments may be "
            "saved in shell history; run `jdx configure` and paste it at the "
            "hidden prompt instead"
        )
    credential: str | None = None
    if args.auth == "token":
        credential = getpass.getpass(
            "Jupyter token (input hidden; press Enter to use token from URL): "
        ).strip()
        credential = credential or url_token
        if not credential:
            raise ConfigurationError("no Jupyter token was provided")
    elif args.auth == "cookie":
        credential = getpass.getpass(
            "Jupyter Cookie header (input hidden): "
        ).strip()
        if not credential:
            raise ConfigurationError("no Jupyter cookie was provided")

    path = (
        os.path.expanduser(args.config)
        if args.config
        else str(default_config_path())
    )
    payload: dict[str, Any] = {
        "url": base_url,
        "verify_tls": not args.no_verify_tls,
    }
    if args.auth == "token":
        payload["token"] = credential
    elif args.auth == "cookie":
        payload["cookie"] = credential
    for key in ("terminal", "cwd", "origin", "ca_bundle"):
        value = getattr(args, key)
        if value:
            payload[key] = value
    absolute_path = Path(os.path.abspath(path))
    save_config_file(absolute_path, payload)
    settings = Settings(
        base_url=base_url,
        token=credential if args.auth == "token" else None,
        cookie=credential if args.auth == "cookie" else None,
        verify_tls=not args.no_verify_tls,
        ca_bundle=Path(args.ca_bundle).expanduser() if args.ca_bundle else None,
        terminal=args.terminal,
        cwd=args.cwd,
    )
    return {
        "config_saved": True,
        "config_path": (
            str(absolute_path)
            if args.show_config
            else _private_path_label(absolute_path)
        ),
        "config": settings.public_summary(
            reveal_sensitive=args.show_config
        ),
    }


def _private_path_label(path: Path) -> str:
    """Display a useful config path without leaking the local account name."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return "<custom private path>"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.action == "configure":
            result = _configure(args)
        else:
            settings = Settings.from_env()
            result = asyncio.run(_run(args, settings))
    except (ConfigurationError, GatewayError, OSError) as exc:
        payload = {
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(
            json.dumps(
                {"ok": False, "error": "KeyboardInterrupt", "message": "cancelled"}
            ),
            file=sys.stderr,
        )
        return 130

    payload: dict[str, Any] = {"ok": True, "result": result}
    indent = 2 if args.pretty or os.environ.get("JUPYDEX_PRETTY") else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
