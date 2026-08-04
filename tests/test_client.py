from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

import httpx
import websockets
from websockets.exceptions import WebSocketException

from jupydex.client import (
    AuthenticationError,
    GatewayError,
    JupyterTerminalClient,
    ProxySupportError,
    RemoteOutcomeUnknownError,
    validate_operation_id,
    validate_operation_state,
    validate_terminal_name,
)
from jupydex.config import Settings


def _markers(script: str) -> tuple[str, str]:
    start = script.split("printf '\\n", 1)[1].split("\\n'", 1)[0]
    done = script.split("printf '\\n", 2)[2].split(":%s", 1)[0]
    return start, done


ResponseFactory = Callable[[str], list[str | bytes | None]]


class _FakeWebSocket:
    def __init__(
        self,
        responses: list[str | bytes | None],
        *,
        response_factory: ResponseFactory | None = None,
    ) -> None:
        self.responses: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        for response in responses:
            self.responses.put_nowait(response)
        self.sent: list[str] = []
        self.response_factory = response_factory or self._default_response

    async def send(self, message: str) -> None:
        self.sent.append(message)
        decoded = json.loads(message)
        if decoded[0] == "stdin" and "__JUPYDEX_DONE_" in decoded[1]:
            script = decoded[1]
            for response in self.response_factory(script):
                await self.responses.put(response)

    @staticmethod
    def _default_response(script: str) -> list[str]:
        start, done = _markers(script)
        return [
            json.dumps(
                [
                    "stdout",
                    "printf 'echoed "
                    f"{start} text'\r\n{start}\r\nhello\r\n{done}:7\r\n",
                ]
            )
        ]

    async def recv(self) -> str | bytes | None:
        return await self.responses.get()


class _ExecutingWebSocket(_FakeWebSocket):
    def __init__(self, *, close_after_execution: bool = False) -> None:
        super().__init__([])
        self.close_after_execution = close_after_execution

    async def send(self, message: str) -> None:
        self.sent.append(message)
        decoded = json.loads(message)
        if decoded[0] != "stdin" or "__JUPYDEX_DONE_" not in decoded[1]:
            return
        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-e",
            "-c",
            decoded[1].rstrip("\r"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        if self.close_after_execution:
            await self.responses.put("")
        else:
            await self.responses.put(
                json.dumps(
                    ["stdout", output.decode("utf-8", errors="replace")]
                )
            )


class _FakeConnection:
    def __init__(self, websocket: _FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> _FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *_: object) -> None:
        return None


class _FailingCloseConnection(_FakeConnection):
    async def __aexit__(self, *_: object) -> None:
        raise WebSocketException("close failed")


class _MissingSocksConnection:
    async def __aenter__(self) -> object:
        raise ImportError(
            "python-socks is required for SOCKS proxy support but isn't installed"
        )

    async def __aexit__(self, *_: object) -> None:
        return None


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_rest_client_receives_the_same_explicit_proxy_policy(self) -> None:
        proxy = "socks5://user:secret@proxy.example:1080"
        with mock.patch("jupydex.client.httpx.AsyncClient") as constructor:
            JupyterTerminalClient(
                Settings(
                    base_url="https://example.test",
                    proxy_mode=proxy,
                )
            )
        kwargs = constructor.call_args.kwargs
        self.assertEqual(kwargs["proxy"], proxy)
        self.assertFalse(kwargs["trust_env"])

    async def test_rest_missing_socks_dependency_is_structured(self) -> None:
        error = ImportError(
            "Using SOCKS proxy, but the socksio package is not installed"
        )
        with mock.patch(
            "jupydex.client.httpx.AsyncClient",
            side_effect=error,
        ):
            with self.assertRaises(ProxySupportError) as raised:
                JupyterTerminalClient(
                    Settings(
                        base_url="https://example.test",
                        proxy_mode="socks5://proxy.example:1080",
                    )
                )
        self.assertEqual(raised.exception.proxy_mode, "explicit_socks")
        self.assertNotIn("proxy.example", str(raised.exception))

    async def test_terminal_name_rejects_hyphens_before_network_access(self) -> None:
        with self.assertRaises(GatewayError):
            validate_terminal_name("agent-terminal")

    async def test_operation_identifiers_are_strict(self) -> None:
        self.assertEqual(validate_operation_id("deploy_123"), "deploy_123")
        self.assertEqual(
            validate_operation_state("PIDS_VERIFIED"),
            "PIDS_VERIFIED",
        )
        with self.assertRaises(GatewayError):
            validate_operation_id("../escape")
        with self.assertRaises(GatewayError):
            validate_operation_state("term-sent")

    async def test_rest_list_and_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/api/status"):
                return httpx.Response(
                    200, json={"connections": 1}, headers={"content-type": "application/json"}
                )
            return httpx.Response(
                200,
                json=[{"name": "codex"}],
                headers={"content-type": "application/json"},
            )

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://example.test/"
        )
        client = JupyterTerminalClient(
            Settings(base_url="https://example.test"), http_client=http
        )
        result = await client.status()
        self.assertTrue(result["connected"])
        self.assertTrue(result["rest_connected"])
        self.assertIsNone(result["websocket_connected"])
        self.assertFalse(result["websocket"]["checked"])
        self.assertEqual(result["terminal_count"], 1)
        self.assertEqual(
            result["config"]["base_url"], "https://<redacted>"
        )
        await http.aclose()

    async def test_doctor_reports_websocket_handshake_separately(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload: object = (
                {"connections": 1}
                if request.url.path.endswith("/api/status")
                else [{"name": "codex"}]
            )
            return httpx.Response(
                200,
                json=payload,
                headers={"content-type": "application/json"},
            )

        seen: dict[str, object] = {}

        def connector(_: str, **kwargs: object) -> _FakeConnection:
            seen.update(kwargs)
            return _FakeConnection(_FakeWebSocket([]))

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://example.test/",
        )
        client = JupyterTerminalClient(
            Settings(
                base_url="https://example.test",
                terminal="codex",
                proxy_mode="none",
            ),
            http_client=http,
            connector=connector,
        )
        result = await client.status(check_websocket=True)
        self.assertTrue(result["rest_connected"])
        self.assertTrue(result["websocket_connected"])
        self.assertTrue(result["websocket"]["checked"])
        self.assertIsNone(seen["proxy"])
        await http.aclose()

    async def test_doctor_redacts_missing_socks_dependency(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload: object = (
                {"connections": 1}
                if request.url.path.endswith("/api/status")
                else [{"name": "codex"}]
            )
            return httpx.Response(
                200,
                json=payload,
                headers={"content-type": "application/json"},
            )

        seen: dict[str, object] = {}

        def connector(_: str, **kwargs: object) -> _MissingSocksConnection:
            seen.update(kwargs)
            return _MissingSocksConnection()

        proxy = "socks5://user:secret@proxy.example:1080"
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://jupyter.example/",
        )
        client = JupyterTerminalClient(
            Settings(
                base_url="https://jupyter.example",
                terminal="codex",
                proxy_mode=proxy,
            ),
            http_client=http,
            connector=connector,
        )
        result = await client.status(check_websocket=True)
        rendered = repr(result)
        self.assertTrue(result["rest_connected"])
        self.assertFalse(result["websocket_connected"])
        self.assertEqual(
            result["websocket"]["error"],
            ProxySupportError.__name__,
        )
        self.assertEqual(result["websocket"]["proxy_mode"], "explicit_socks")
        self.assertEqual(seen["proxy"], proxy)
        self.assertNotIn("proxy.example", rendered)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("jupyter.example", rendered)
        await http.aclose()

    async def test_doctor_uses_available_explicit_socks_connector(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload: object = (
                {"connections": 1}
                if request.url.path.endswith("/api/status")
                else [{"name": "codex"}]
            )
            return httpx.Response(
                200,
                json=payload,
                headers={"content-type": "application/json"},
            )

        proxy = "socks5://proxy.example:1080"
        seen: dict[str, object] = {}

        def connector(_: str, **kwargs: object) -> _FakeConnection:
            seen.update(kwargs)
            return _FakeConnection(_FakeWebSocket([]))

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://example.test/",
        )
        client = JupyterTerminalClient(
            Settings(
                base_url="https://example.test",
                terminal="codex",
                proxy_mode=proxy,
            ),
            http_client=http,
            connector=connector,
        )
        result = await client.status(check_websocket=True)
        self.assertTrue(result["websocket_connected"])
        self.assertEqual(seen["proxy"], proxy)
        self.assertEqual(result["websocket"]["proxy_mode"], "explicit_socks")
        await http.aclose()

    async def test_login_redirect_is_an_authentication_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "/login"})

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://example.test/"
        )
        client = JupyterTerminalClient(
            Settings(base_url="https://example.test"), http_client=http
        )
        with self.assertRaises(AuthenticationError):
            await client.list_terminals()
        await http.aclose()

    async def test_http_error_never_reflects_token_or_response_body(self) -> None:
        token = "private-test-token"

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500,
                text=f"upstream reflected {token}",
                headers={"content-type": "text/plain"},
            )

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://example.test/",
        )
        client = JupyterTerminalClient(
            Settings(base_url="https://example.test", token=token),
            http_client=http,
        )
        with self.assertRaises(GatewayError) as raised:
            await client.list_terminals()
        self.assertNotIn(token, str(raised.exception))
        self.assertNotIn("upstream reflected", str(raised.exception))
        await http.aclose()

    async def test_execute_reads_exit_marker(self) -> None:
        websocket = _FakeWebSocket([])

        def connector(*_: object, **__: object) -> _FakeConnection:
            return _FakeConnection(websocket)

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json=[], headers={"content-type": "application/json"}
                )
            ),
            base_url="https://example.test/",
        )
        client = JupyterTerminalClient(
            Settings(base_url="https://example.test"),
            http_client=http,
            connector=connector,
        )
        result = await client.execute("codex", "false", timeout=2)
        self.assertEqual(result.output, "hello")
        self.assertEqual(result.exit_code, 7)
        self.assertFalse(result.timed_out)
        sent_script = json.loads(websocket.sent[0])[1]
        self.assertEqual(sent_script.count("\r"), 1)
        self.assertTrue(sent_script.endswith("\r"))
        self.assertIn("bash -lc ", sent_script)
        self.assertNotIn("command", result.as_dict())
        self.assertEqual(result.as_dict(include_command=True)["command"], "false")
        await http.aclose()

    async def test_set_e_failure_stays_inside_child_shell(self) -> None:
        websocket = _ExecutingWebSocket()

        def connector(*_: object, **__: object) -> _FakeConnection:
            return _FakeConnection(websocket)

        http = self._http_client()
        client = JupyterTerminalClient(
            Settings(base_url="https://example.test"),
            http_client=http,
            connector=connector,
        )
        result = await client.execute(
            "codex",
            "set -Eeuo pipefail; false",
            timeout=2,
        )
        self.assertEqual(result.exit_code, 1)
        self.assertFalse(result.timed_out)
        sent_script = json.loads(websocket.sent[0])[1]
        self.assertIn("bash -lc ", sent_script)
        await http.aclose()

    async def test_completion_marker_can_span_frames(self) -> None:
        def split_response(script: str) -> list[str]:
            start, done = _markers(script)
            midpoint = len(done) // 2
            return [
                json.dumps(
                    ["stdout", f"{start}\r\npayload\r\n{done[:midpoint]}"]
                ),
                json.dumps(["stdout", f"{done[midpoint:]}:0\r\n"]),
            ]

        websocket = _FakeWebSocket([], response_factory=split_response)

        def connector(*_: object, **__: object) -> _FakeConnection:
            return _FakeConnection(websocket)

        http = self._http_client()
        client = JupyterTerminalClient(
            Settings(base_url="https://example.test"),
            http_client=http,
            connector=connector,
        )
        result = await client.execute("codex", "true", timeout=2)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, "payload")
        await http.aclose()

    async def test_close_failure_does_not_override_confirmed_result(self) -> None:
        websocket = _FakeWebSocket([])

        def connector(*_: object, **__: object) -> _FailingCloseConnection:
            return _FailingCloseConnection(websocket)

        http = self._http_client()
        client = JupyterTerminalClient(
            Settings(base_url="https://example.test"),
            http_client=http,
            connector=connector,
        )
        result = await client.execute("codex", "true", timeout=2)
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.output, "hello")
        await http.aclose()

    async def test_non_json_and_binary_frames_are_annotated(self) -> None:
        def framed_response(script: str) -> list[str | bytes]:
            start, done = _markers(script)
            return [
                json.dumps(["stdout", f"{start}\r\n"]),
                b"\xffnot-json",
                json.dumps(
                    ["stdout", f"finished\r\n{done}:0\r\n"]
                ),
            ]

        websocket = _FakeWebSocket([], response_factory=framed_response)

        def connector(*_: object, **__: object) -> _FakeConnection:
            return _FakeConnection(websocket)

        http = self._http_client()
        client = JupyterTerminalClient(
            Settings(base_url="https://example.test"),
            http_client=http,
            connector=connector,
        )
        result = await client.execute("codex", "true", timeout=2, raw=True)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("[JUPYDEX_NON_JSON_FRAME]", result.output)
        self.assertIn("finished", result.output)
        await http.aclose()

    async def test_reconnects_same_terminal_without_resending_command(self) -> None:
        first = _FakeWebSocket(
            [],
            response_factory=lambda _: [""],
        )
        second = _FakeWebSocket([])
        sockets = [first, second]
        seen_urls: list[str] = []

        def connector(url: str, **__: object) -> _FakeConnection:
            seen_urls.append(url)
            websocket = sockets.pop(0)
            if websocket is second:
                script = json.loads(first.sent[0])[1]
                start, done = _markers(script)
                websocket.responses.put_nowait(
                    json.dumps(
                        ["stdout", f"{start}\r\nrecovered\r\n{done}:0\r\n"]
                    )
                )
            return _FakeConnection(websocket)

        http = self._http_client()
        client = JupyterTerminalClient(
            Settings(base_url="https://example.test"),
            http_client=http,
            connector=connector,
            reconnect_delays=(0,),
        )
        result = await client.execute("codex", "true", timeout=2)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, "recovered")
        self.assertEqual(len(seen_urls), 2)
        self.assertEqual(seen_urls[0], seen_urls[1])
        self.assertEqual(len(first.sent), 1)
        self.assertEqual(second.sent, [])
        await http.aclose()

    async def test_empty_frames_report_unknown_and_never_delete_terminal(self) -> None:
        requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            return httpx.Response(
                204,
                headers={"content-type": "application/json"},
            )

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://example.test/",
        )
        sockets = [
            _FakeWebSocket([], response_factory=lambda _: [""]),
            _FakeWebSocket([""]),
            _FakeWebSocket([""]),
            _FakeWebSocket([""]),
        ]
        seen_urls: list[str] = []
        sleep_delays: list[float] = []

        def connector(url: str, **__: object) -> _FakeConnection:
            seen_urls.append(url)
            return _FakeConnection(sockets.pop(0))

        async def sleeper(delay: float) -> None:
            sleep_delays.append(delay)

        client = JupyterTerminalClient(
            Settings(base_url="https://example.test"),
            http_client=http,
            connector=connector,
            sleeper=sleeper,
        )
        with self.assertRaisesRegex(
            RemoteOutcomeUnknownError,
            "remote outcome unknown",
        ) as raised:
            await client.execute("codex", "true", timeout=20)
        self.assertEqual(raised.exception.terminal, "codex")
        self.assertTrue(raised.exception.terminal_retained)
        self.assertEqual(raised.exception.reconnect_attempts, 3)
        self.assertEqual(len(seen_urls), 4)
        self.assertEqual(len(set(seen_urls)), 1)
        self.assertEqual(sleep_delays, [1.0, 2.0, 4.0])
        self.assertFalse(any(method == "DELETE" for method, _ in requests))
        await http.aclose()

    async def test_operation_state_recovers_after_unknown_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            good_http = self._http_client()

            def good_connector(*_: object, **__: object) -> _FakeConnection:
                return _FakeConnection(_ExecutingWebSocket())

            good_client = JupyterTerminalClient(
                Settings(base_url="https://example.test"),
                http_client=good_http,
                connector=good_connector,
            )
            started = await good_client.begin_operation(
                "codex",
                directory,
                operation_id="deploy_test",
                timeout=2,
            )
            self.assertEqual(started["state"], "STARTED")

            unknown_http = self._http_client()

            def unknown_connector(*_: object, **__: object) -> _FakeConnection:
                return _FakeConnection(
                    _ExecutingWebSocket(close_after_execution=True)
                )

            unknown_client = JupyterTerminalClient(
                Settings(base_url="https://example.test"),
                http_client=unknown_http,
                connector=unknown_connector,
                reconnect_delays=(),
            )
            with self.assertRaises(RemoteOutcomeUnknownError) as raised:
                await unknown_client.set_operation_state(
                    "codex",
                    directory,
                    "deploy_test",
                    "TERM_SENT",
                    timeout=2,
                )
            self.assertEqual(
                raised.exception.operation_id,
                "deploy_test",
            )

            recovered = await good_client.get_operation_state(
                "codex",
                directory,
                "deploy_test",
                timeout=2,
            )
            self.assertTrue(recovered["exists"])
            self.assertEqual(recovered["state"], "TERM_SENT")
            self.assertEqual(
                Path(str(recovered["status_file"])).read_text().strip(),
                "TERM_SENT",
            )
            await good_http.aclose()
            await unknown_http.aclose()

    async def test_execute_over_real_websocket_transport(self) -> None:
        seen: dict[str, str] = {}

        async def handler(connection: object) -> None:
            request = connection.request
            seen["path"] = request.path
            seen["authorization"] = request.headers["Authorization"]
            message = await connection.recv()
            script = json.loads(message)[1]
            start = script.split("printf '\\n", 1)[1].split("\\n'", 1)[0]
            done = script.split("printf '\\n", 2)[2].split(":%s", 1)[0]
            await connection.send(
                json.dumps(["stdout", f"{start}\r\ntransport-ok\r\n{done}:0\r\n"])
            )

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json=[], headers={"content-type": "application/json"}
                )
            ),
            base_url=f"http://127.0.0.1:{port}/",
        )
        with mock.patch.dict(
            os.environ,
            {
                "ALL_PROXY": "socks5://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1",
                "no_proxy": "127.0.0.1",
            },
            clear=True,
        ):
            client = JupyterTerminalClient(
                Settings(
                    base_url=f"http://127.0.0.1:{port}",
                    token="test-token",
                ),
                http_client=http,
            )
            try:
                result = await client.execute(
                    "codex_terminal", "true", timeout=2
                )
            finally:
                server.close()
                await server.wait_closed()
                await http.aclose()
        self.assertEqual(result.output, "transport-ok")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(seen["path"], "/terminals/websocket/codex_terminal")
        self.assertEqual(seen["authorization"], "token test-token")

    @staticmethod
    def _http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json=[],
                    headers={"content-type": "application/json"},
                )
            ),
            base_url="https://example.test/",
        )


if __name__ == "__main__":
    unittest.main()
