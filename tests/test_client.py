from __future__ import annotations

import asyncio
import json
import unittest

import httpx
import websockets

from jupydex.client import (
    AuthenticationError,
    GatewayError,
    JupyterTerminalClient,
    validate_terminal_name,
)
from jupydex.config import Settings


class _FakeWebSocket:
    def __init__(self, responses: list[str]) -> None:
        self.responses = asyncio.Queue()
        for response in responses:
            self.responses.put_nowait(response)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)
        decoded = json.loads(message)
        if decoded[0] == "stdin" and "__JUPYDEX_DONE_" in decoded[1]:
            script = decoded[1]
            start = script.split("printf '\\n", 1)[1].split("\\n'", 1)[0]
            done = script.split("printf '\\n", 2)[2].split(":%s", 1)[0]
            await self.responses.put(
                json.dumps(
                    [
                        "stdout",
                        "printf 'echoed "
                        f"{start} text'\r\n{start}\r\nhello\r\n{done}:7\r\n",
                    ]
                )
            )

    async def recv(self) -> str:
        return await self.responses.get()


class _FakeConnection:
    def __init__(self, websocket: _FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> _FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *_: object) -> None:
        return None


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_name_rejects_hyphens_before_network_access(self) -> None:
        with self.assertRaises(GatewayError):
            validate_terminal_name("agent-terminal")

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
        self.assertEqual(result["terminal_count"], 1)
        self.assertEqual(
            result["config"]["base_url"], "https://<redacted>"
        )
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
        self.assertNotIn("command", result.as_dict())
        self.assertEqual(result.as_dict(include_command=True)["command"], "false")
        await http.aclose()

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
        client = JupyterTerminalClient(
            Settings(base_url=f"http://127.0.0.1:{port}", token="test-token"),
            http_client=http,
        )
        try:
            result = await client.execute("codex_terminal", "true", timeout=2)
        finally:
            server.close()
            await server.wait_closed()
            await http.aclose()
        self.assertEqual(result.output, "transport-ok")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(seen["path"], "/terminals/websocket/codex_terminal")
        self.assertEqual(seen["authorization"], "token test-token")


if __name__ == "__main__":
    unittest.main()
