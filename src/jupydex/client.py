from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import signal
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncContextManager, AsyncIterator, Callable
from urllib.parse import quote

import httpx
import websockets

from .config import Settings
from .output import clean_terminal_output


class GatewayError(RuntimeError):
    """Base error for gateway operations."""


class AuthenticationError(GatewayError):
    """Raised when Jupyter authentication is rejected or redirects to login."""


class TerminalNotFoundError(GatewayError):
    """Raised when a requested terminal does not exist."""


_VALID_TERMINAL_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_DETACH = object()


def validate_terminal_name(name: str) -> str:
    if not _VALID_TERMINAL_NAME.fullmatch(name):
        raise GatewayError(
            "terminal names must contain only ASCII letters, digits, and "
            "underscores; Jupyter's WebSocket route rejects other characters"
        )
    return name


@dataclass(slots=True)
class CommandResult:
    terminal: str
    command: str
    output: str
    exit_code: int | None
    timed_out: bool
    elapsed_seconds: float

    def as_dict(self, *, include_command: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "terminal": self.terminal,
            "output": self.output,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }
        if include_command:
            result["command"] = self.command
        return result


Connector = Callable[..., AsyncContextManager[Any]]


class JupyterTerminalClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        connector: Connector | None = None,
    ) -> None:
        self.settings = settings
        self._owns_http_client = http_client is None
        ssl_context = settings.ssl_context()
        self._http = http_client or httpx.AsyncClient(
            base_url=f"{settings.base_url}/",
            headers=settings.http_headers,
            timeout=settings.request_timeout,
            verify=ssl_context if ssl_context is not None else True,
            follow_redirects=False,
        )
        self._connector = connector or websockets.connect

    async def __aenter__(self) -> "JupyterTerminalClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def status(
        self, *, reveal_sensitive: bool = False
    ) -> dict[str, object]:
        status = await self._request("GET", "api/status")
        terminals = await self.list_terminals()
        configured = (
            next(
                (
                    terminal
                    for terminal in terminals
                    if terminal.get("name") == self.settings.terminal
                ),
                None,
            )
            if self.settings.terminal
            else None
        )
        return {
            "connected": True,
            "server": status,
            "terminal_count": len(terminals),
            "configured_terminal": (
                configured
                if reveal_sensitive
                else {"configured": True, "online": configured is not None}
                if self.settings.terminal
                else {"configured": False, "online": None}
            ),
            "config": self.settings.public_summary(
                reveal_sensitive=reveal_sensitive
            ),
        }

    async def list_terminals(self) -> list[dict[str, object]]:
        payload = await self._request("GET", "api/terminals")
        if not isinstance(payload, list):
            raise GatewayError("Jupyter returned a non-list terminal response")
        return payload

    async def create_terminal(
        self, *, name: str | None = None, cwd: str | None = None
    ) -> dict[str, object]:
        if name:
            validate_terminal_name(name)
        body = {"name": name} if name else None
        terminal = await self._request("POST", "api/terminals", json_body=body)
        if not isinstance(terminal, dict) or not terminal.get("name"):
            raise GatewayError("Jupyter did not return a terminal name")
        actual_name = str(terminal["name"])
        if cwd:
            result = await self.execute(
                actual_name,
                "pwd",
                cwd=cwd,
                timeout=min(15.0, self.settings.request_timeout),
            )
            terminal["cwd_result"] = result.as_dict()
        return terminal

    async def delete_terminal(self, name: str) -> dict[str, object]:
        validate_terminal_name(name)
        await self._request(
            "DELETE", f"api/terminals/{quote(name, safe='')}", expect_json=False
        )
        return {"terminal": name, "deleted": True}

    async def watch(
        self,
        name: str,
        *,
        seconds: float = 2.0,
        max_chars: int = 40_000,
        raw: bool = False,
    ) -> dict[str, object]:
        validate_terminal_name(name)
        if seconds <= 0:
            raise GatewayError("watch duration must be positive")
        output = await self._collect(name, seconds=seconds, max_chars=max_chars)
        return {
            "terminal": name,
            "seconds": seconds,
            "output": output if raw else clean_terminal_output(output),
        }

    async def send(
        self,
        name: str,
        text: str,
        *,
        append_enter: bool = False,
        listen_seconds: float = 0.5,
        raw: bool = False,
    ) -> dict[str, object]:
        validate_terminal_name(name)
        payload = text + ("\r" if append_enter else "")
        started = asyncio.get_running_loop().time()
        chunks: list[str] = []
        async with self._connect(name) as websocket:
            await websocket.send(json.dumps(["stdin", payload]))
            await self._receive_for(
                websocket, chunks, duration=max(0.0, listen_seconds)
            )
        output = "".join(chunks)
        return {
            "terminal": name,
            "sent_chars": len(payload),
            "output": output if raw else clean_terminal_output(output),
            "elapsed_seconds": round(
                asyncio.get_running_loop().time() - started, 3
            ),
        }

    async def interrupt(self, name: str, *, listen_seconds: float = 1.0) -> dict[str, object]:
        result = await self.send(
            name, "\x03", listen_seconds=listen_seconds, raw=False
        )
        result["interrupted"] = True
        return result

    async def interactive_shell(self, name: str) -> dict[str, object]:
        """Bridge the local TTY to a Jupyter terminal until the remote shell exits."""
        validate_terminal_name(name)
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise GatewayError("interactive shell requires a local TTY")
        try:
            import termios
            import tty
        except ImportError as exc:
            raise GatewayError(
                "interactive shell currently requires a POSIX terminal"
            ) from exc

        input_fd = sys.stdin.fileno()
        output_fd = sys.stdout.fileno()
        loop = asyncio.get_running_loop()
        old_attributes = termios.tcgetattr(input_fd)
        outbound: asyncio.Queue[list[object] | object | None] = asyncio.Queue()

        def queue_input() -> None:
            data = os.read(input_fd, 4096)
            if data:
                detach_at = data.find(b"\x1d")
                if detach_at >= 0:
                    before_detach = data[:detach_at]
                    if before_detach:
                        outbound.put_nowait(
                            [
                                "stdin",
                                before_detach.decode("utf-8", errors="replace"),
                            ]
                        )
                    loop.remove_reader(input_fd)
                    outbound.put_nowait(_DETACH)
                    return
                outbound.put_nowait(
                    ["stdin", data.decode("utf-8", errors="replace")]
                )
            else:
                loop.remove_reader(input_fd)
                outbound.put_nowait(None)

        def queue_resize() -> None:
            rows, columns = _terminal_size(input_fd)
            outbound.put_nowait(["set_size", rows, columns, 0, 0])

        exit_code = 0
        try:
            tty.setraw(input_fd)
            loop.add_reader(input_fd, queue_input)
            signal_installed = False
            if hasattr(signal, "SIGWINCH"):
                try:
                    loop.add_signal_handler(signal.SIGWINCH, queue_resize)
                    signal_installed = True
                except (NotImplementedError, RuntimeError):
                    pass

            async with self._connect(name) as websocket:
                rows, columns = _terminal_size(input_fd)
                await websocket.send(
                    json.dumps(["set_size", rows, columns, 0, 0])
                )

                async def send_input() -> str:
                    while True:
                        message = await outbound.get()
                        if message is _DETACH:
                            os.write(output_fd, b"\r\n[Jupydex detached]\r\n")
                            return "detach"
                        if message is None:
                            await websocket.send(json.dumps(["stdin", "\x04"]))
                            return "eof"
                        assert isinstance(message, list)
                        await websocket.send(json.dumps(message))

                async def receive_output() -> int:
                    while True:
                        message = await websocket.recv()
                        if isinstance(message, bytes):
                            message = message.decode("utf-8", errors="replace")
                        try:
                            payload = json.loads(message)
                        except json.JSONDecodeError:
                            os.write(output_fd, message.encode("utf-8", errors="replace"))
                            continue
                        if (
                            isinstance(payload, list)
                            and len(payload) >= 2
                            and payload[0] == "stdout"
                            and isinstance(payload[1], str)
                        ):
                            os.write(
                                output_fd,
                                payload[1].encode(
                                    "utf-8", errors="surrogateescape"
                                ),
                            )
                        elif (
                            isinstance(payload, list)
                            and payload
                            and payload[0] == "disconnect"
                        ):
                            return (
                                int(payload[1])
                                if len(payload) > 1
                                and isinstance(payload[1], int)
                                else 0
                            )

                sender = asyncio.create_task(send_input())
                receiver = asyncio.create_task(receive_output())
                done, pending = await asyncio.wait(
                    {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
                )
                if receiver in done:
                    exit_code = receiver.result()
                else:
                    sender_result = sender.result()
                    if sender_result == "detach":
                        receiver.cancel()
                        exit_code = 0
                    else:
                        exit_code = await receiver
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            try:
                loop.remove_reader(input_fd)
            except (KeyError, ValueError):
                pass
            if "signal_installed" in locals() and signal_installed:
                loop.remove_signal_handler(signal.SIGWINCH)
            termios.tcsetattr(input_fd, termios.TCSADRAIN, old_attributes)
        return {"terminal": name, "exit_code": exit_code}

    async def execute(
        self,
        name: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float = 60.0,
        max_chars: int = 200_000,
        raw: bool = False,
        interrupt_on_timeout: bool = False,
    ) -> CommandResult:
        validate_terminal_name(name)
        if not command.strip():
            raise GatewayError("command must not be empty")
        if timeout <= 0:
            raise GatewayError("timeout must be positive")

        effective_cwd = cwd or self.settings.cwd
        evaluated_command = f"eval {shlex.quote(command)}"
        actual_command = evaluated_command
        if effective_cwd:
            actual_command = (
                f"if cd -- {shlex.quote(effective_cwd)}; "
                f"then {evaluated_command}; else false; fi"
            )

        nonce = uuid.uuid4().hex
        start_marker = f"__JUPYDEX_START_{nonce}__"
        done_marker = f"__JUPYDEX_DONE_{nonce}__"
        start_pattern = re.compile(
            r"(?:^|\r?\n)" + re.escape(start_marker) + r"\r?\n"
        )
        script = (
            f"printf '\\n{start_marker}\\n'; "
            f"{actual_command}; "
            "__jupydex_status=$?; "
            f"printf '\\n{done_marker}:%s\\n' \"$__jupydex_status\"\r"
        )
        done_pattern = re.compile(
            re.escape(done_marker) + r":(?P<status>\d{1,3})(?:\r?\n|$)"
        )

        started = asyncio.get_running_loop().time()
        chunks: list[str] = []
        exit_code: int | None = None
        timed_out = False

        async with self._connect(name) as websocket:
            # Let Jupyter send any reconnect scrollback before the new command.
            await self._receive_for(websocket, chunks, duration=0.15)
            chunks.clear()
            await websocket.send(json.dumps(["stdin", script]))
            deadline = started + timeout
            while exit_code is None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    timed_out = True
                    if interrupt_on_timeout:
                        await websocket.send(json.dumps(["stdin", "\x03"]))
                    break
                try:
                    message = await asyncio.wait_for(
                        websocket.recv(), timeout=min(remaining, 1.0)
                    )
                except asyncio.TimeoutError:
                    continue
                text = _stdout_from_message(message)
                if text is None:
                    continue
                chunks.append(text)
                if sum(map(len, chunks)) > max_chars * 2:
                    chunks = ["".join(chunks)[-max_chars * 2 :]]
                match = done_pattern.search("".join(chunks))
                if match:
                    exit_code = int(match.group("status"))

        output = "".join(chunks)
        start_match = start_pattern.search(output)
        if start_match:
            output = output[start_match.end() :]
        done_match = done_pattern.search(output)
        if done_match:
            output = output[: done_match.start()]
        output = output[-max_chars:]
        if not raw:
            output = clean_terminal_output(output)
        elapsed = asyncio.get_running_loop().time() - started
        return CommandResult(
            terminal=name,
            command=command,
            output=output.rstrip(),
            exit_code=exit_code,
            timed_out=timed_out,
            elapsed_seconds=elapsed,
        )

    async def _collect(self, name: str, *, seconds: float, max_chars: int) -> str:
        chunks: list[str] = []
        async with self._connect(name) as websocket:
            await self._receive_for(websocket, chunks, duration=seconds)
        return "".join(chunks)[-max_chars:]

    async def _receive_for(
        self, websocket: Any, chunks: list[str], *, duration: float
    ) -> None:
        deadline = asyncio.get_running_loop().time() + duration
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            try:
                message = await asyncio.wait_for(
                    websocket.recv(), timeout=min(remaining, 0.25)
                )
            except asyncio.TimeoutError:
                continue
            text = _stdout_from_message(message)
            if text is not None:
                chunks.append(text)

    @asynccontextmanager
    async def _connect(self, name: str) -> AsyncIterator[Any]:
        url = (
            f"{self.settings.websocket_url_prefix}/terminals/websocket/"
            f"{quote(name, safe='')}"
        )
        kwargs: dict[str, object] = {
            "additional_headers": self.settings.http_headers,
            "origin": self.settings.websocket_origin,
            "open_timeout": self.settings.request_timeout,
            "max_size": None,
            "ping_interval": 20,
        }
        ssl_context = self.settings.ssl_context()
        if ssl_context is not None:
            kwargs["ssl"] = ssl_context
        try:
            async with self._connector(url, **kwargs) as websocket:
                yield websocket
        except websockets.exceptions.InvalidStatus as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code in {401, 403}:
                raise AuthenticationError(
                    f"Jupyter rejected WebSocket authentication ({status_code})"
                ) from exc
            raise GatewayError(f"Jupyter WebSocket rejected the connection: {exc}") from exc
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            raise GatewayError(f"Jupyter WebSocket failed: {exc}") from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        expect_json: bool = True,
    ) -> Any:
        try:
            request_kwargs: dict[str, object] = {}
            if json_body is not None:
                request_kwargs["json"] = json_body
            response = await self._http.request(method, path, **request_kwargs)
        except httpx.HTTPError as exc:
            raise GatewayError(f"Jupyter request failed: {exc}") from exc

        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location", "")
            raise AuthenticationError(
                f"Jupyter redirected the API request to {location or 'a login page'}"
            )
        if response.status_code in {401, 403}:
            raise AuthenticationError(
                f"Jupyter rejected authentication ({response.status_code})"
            )
        if response.status_code == 404 and "/terminals/" in f"/{path}":
            raise TerminalNotFoundError(f"terminal not found: {path.rsplit('/', 1)[-1]}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            excerpt = response.text[:300].replace("\n", " ")
            raise GatewayError(
                f"Jupyter returned HTTP {response.status_code}: {excerpt}"
            ) from exc

        if not expect_json or response.status_code == 204:
            return None
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            excerpt = response.text[:200].replace("\n", " ")
            raise AuthenticationError(
                "Jupyter returned non-JSON content; the URL may point to a login "
                f"or UI page: {excerpt}"
            )
        return response.json()


def _stdout_from_message(message: str | bytes) -> str | None:
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return message
    if (
        isinstance(payload, list)
        and len(payload) >= 2
        and payload[0] == "stdout"
        and isinstance(payload[1], str)
    ):
        return payload[1]
    return None


def _terminal_size(file_descriptor: int) -> tuple[int, int]:
    size = os.get_terminal_size(file_descriptor)
    return size.lines, size.columns
