from __future__ import annotations

import asyncio
import json
import os
import signal
import unittest
from contextlib import asynccontextmanager

import httpx
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response
from websockets.datastructures import Headers

from jupydex.client import (
    GatewayError, JupyterTerminalClient, RemoteOutcomeUnknownError,
    TerminalNotFoundError,
)
from jupydex.config import Settings
from test_client import _FakeConnection, _FakeWebSocket, _ExecutingWebSocket, _markers


def mock_http(handler=None):
    return httpx.AsyncClient(
        base_url="https://example.test/",
        transport=httpx.MockTransport(handler or (lambda _: httpx.Response(200, json=[]))),
    )


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_cookie_bootstrap_before_first_handshake(self):
        requests = []
        headers = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=[], headers={"set-cookie": "identity=test-session; Path=/; Secure"})

        def connector(url, **kwargs):
            headers.append(kwargs["additional_headers"])
            return _FakeConnection(_FakeWebSocket([]))

        async with mock_http(handler) as http:
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=connector)
            await client.execute("agent", "true", timeout=2)
            await client.execute("agent", "true", timeout=2)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(requests[0].url.path, "/api/terminals")
        self.assertTrue(all(h["Cookie"] == "identity=test-session" for h in headers))

    async def test_create_cookie_reused_without_another_bootstrap(self):
        methods = []
        def handler(request):
            methods.append(request.method)
            return httpx.Response(200, json={"name": "agent"}, headers={"set-cookie": "identity=created; Path=/"})
        def connector(url, **kwargs):
            self.assertEqual(kwargs["additional_headers"]["Cookie"], "identity=created")
            return _FakeConnection(_FakeWebSocket([]))
        async with mock_http(handler) as http:
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=connector)
            await client.create_terminal(name="agent")
            await client.execute("agent", "true", timeout=2)
        self.assertEqual(methods, ["POST"])

    async def test_cookie_domain_and_path_are_respected(self):
        async with mock_http() as http:
            http.cookies.set("wrong_host", "private", domain="other.example", path="/")
            http.cookies.set("wrong_path", "private", domain="example.test", path="/api")
            http.cookies.set("right", "ok", domain="example.test", path="/")
            def connector(url, **kwargs):
                self.assertEqual(kwargs["additional_headers"]["Cookie"], "right=ok")
                return _FakeConnection(_FakeWebSocket([]))
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=connector)
            await client.execute("agent", "true", timeout=2)

    async def test_explicit_cookie_remains_authoritative(self):
        async with mock_http() as http:
            http.cookies.set("identity", "learned", domain="example.test", path="/")
            def connector(url, **kwargs):
                self.assertEqual(kwargs["additional_headers"]["Cookie"], "identity=explicit")
                return _FakeConnection(_FakeWebSocket([]))
            client = JupyterTerminalClient(Settings(base_url="https://example.test", cookie="identity=explicit"), http_client=http, connector=connector)
            await client.execute("agent", "true", timeout=2)

    async def test_secure_cookie_not_sent_over_plaintext_websocket(self):
        async with mock_http(lambda _: httpx.Response(200, json=[], headers={"set-cookie": "identity=secure; Path=/; Secure"})) as http:
            def connector(url, **kwargs):
                self.assertTrue(url.startswith("ws://"))
                self.assertNotIn("Cookie", kwargs["additional_headers"])
                return _FakeConnection(_FakeWebSocket([]))
            client = JupyterTerminalClient(Settings(base_url="http://example.test"), http_client=http, connector=connector)
            await client.execute("agent", "true", timeout=2)

    async def test_invalid_command_rejected_without_network(self):
        async with mock_http() as http:
            connector = unittest.mock.Mock()
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=connector)
            for command, timeout in (("true\x00", 2), ("true", float("nan")), ("true", float("inf"))):
                with self.assertRaises(GatewayError):
                    await client.execute("agent", command, timeout=timeout)
            connector.assert_not_called()

    async def test_exit_code_digits_split_across_frames(self):
        def response(script):
            start, done = _markers(script)
            return [json.dumps(["stdout", f"{start}\r\nresult\r\n{done}:1"]), json.dumps(["stdout", "23\r\n"]) ]
        async with mock_http() as http:
            socket = _FakeWebSocket([], response_factory=response)
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=lambda *a, **k: _FakeConnection(socket))
            result = await client.execute("agent", "exit 123", timeout=2)
        self.assertEqual(result.exit_code, 123)
        self.assertEqual(result.output, "result")

    async def test_reconnect_auth_failure_is_unknown_not_safe_to_retry(self):
        socket = _FakeWebSocket([], response_factory=lambda _: [""])
        calls = 0
        @asynccontextmanager
        async def connector(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise InvalidStatus(Response(403, "Forbidden", Headers(), b"private"))
            yield socket
        async with mock_http() as http:
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=connector, reconnect_delays=(0,))
            with self.assertRaises(RemoteOutcomeUnknownError):
                await client.execute("agent", "true", timeout=2)
        self.assertEqual(len(socket.sent), 1)

    async def test_404_does_not_create_or_replace_terminal(self):
        methods = []
        def handler(request):
            methods.append(request.method)
            return httpx.Response(200, json=[])
        @asynccontextmanager
        async def connector(*args, **kwargs):
            raise InvalidStatus(Response(404, "Missing", Headers(), b"private"))
            yield  # pragma: no cover
        async with mock_http(handler) as http:
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=connector)
            with self.assertRaises(TerminalNotFoundError):
                await client.execute("agent", "true", timeout=2)
        self.assertEqual(methods, ["GET"])

    async def test_handshake_timeout_is_bounded_by_command_deadline(self):
        @asynccontextmanager
        async def connector(*args, **kwargs):
            self.assertLessEqual(kwargs["open_timeout"], 0.05)
            raise asyncio.TimeoutError()
            yield  # pragma: no cover
        async with mock_http() as http:
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=connector, reconnect_delays=())
            with self.assertRaises(GatewayError):
                await client.execute("agent", "true", timeout=0.05)

    async def test_invalid_rest_json_does_not_leak_response(self):
        async with mock_http(lambda _: httpx.Response(200, text="private-upstream", headers={"content-type": "application/json"})) as http:
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http)
            with self.assertRaises(GatewayError) as raised:
                await client.list_terminals()
        self.assertNotIn("private-upstream", str(raised.exception))

    async def test_timeout_does_not_expose_echoed_command(self):
        socket = _FakeWebSocket([], response_factory=lambda script: [json.dumps(["stdout", script])])
        async with mock_http() as http:
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=lambda *a, **k: _FakeConnection(socket))
            result = await client.execute("agent", "echo private-argument", timeout=0.25)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.output, "")
        self.assertEqual(result.as_dict()["remote_outcome"], "unknown")
        self.assertTrue(result.as_dict()["terminal_retained"])
        self.assertEqual(len(socket.sent), 1)

    async def test_large_output_and_malformed_frame_are_redacted(self):
        def response(script):
            start, done = _markers(script)
            return [json.dumps(["stdout", f"{start}\n"]), "private-upstream", json.dumps(["stdout", "x" * 2000 + f"\n{done}:0\n"])]
        socket = _FakeWebSocket([], response_factory=response)
        async with mock_http() as http:
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=lambda *a, **k: _FakeConnection(socket))
            result = await client.execute("agent", "true", max_chars=128, timeout=2)
        self.assertTrue(result.output_truncated)
        self.assertNotIn("private-upstream", result.output)
        self.assertEqual(result.exit_code, 0)
        self.assertLessEqual(len(result.output), 128)

    async def test_multiline_long_script_executes_without_argument_limit(self):
        script = "# " + "comment" * 25000 + "\n" + "value='$HOME $(false)'\nprintf '%s\\n' \"$value\"\nexit 23\n"
        socket = _ExecutingWebSocket()
        async with mock_http() as http:
            client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=lambda *a, **k: _FakeConnection(socket))
            result = await client.execute("agent", script, timeout=5)
        self.assertEqual(result.output, "$HOME $(false)")
        self.assertEqual(result.exit_code, 23)
        framing = json.loads(socket.sent[0])[1]
        self.assertLess(max(map(len, framing.splitlines())), 1024)


@unittest.skipUnless(os.name == "posix", "requires a POSIX PTY and Bash")
class RealPTYTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_script_through_interactive_pty(self):
        master, slave = os.openpty()
        os.set_blocking(master, False)
        process = await asyncio.create_subprocess_exec(
            "/bin/bash", "--noprofile", "--norc", "-i",
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
            env=dict(os.environ, PS1="test> ", PS2="more> ", TERM="dumb", HISTFILE="/dev/null"),
        )
        os.close(slave)
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        def read_ready():
            try:
                data = os.read(master, 65536)
                if data:
                    queue.put_nowait(json.dumps(["stdout", data.decode("utf-8", errors="replace")]))
            except OSError:
                loop.remove_reader(master)
        loop.add_reader(master, read_ready)

        class PTYSocket:
            async def send(self, message):
                data = json.loads(message)[1].encode("utf-8")
                while data:
                    try:
                        count = os.write(master, data[:512])
                        data = data[count:]
                    except BlockingIOError:
                        await asyncio.sleep(0.001)
            async def recv(self):
                return await queue.get()

        try:
            async with mock_http() as http:
                client = JupyterTerminalClient(Settings(base_url="https://example.test"), http_client=http, connector=lambda *a, **k: _FakeConnection(PTYSocket()))
                script = "# " + "x" * 12000 + "\ncat <<'DATA'\n$HOME 'quotes' $(false)\nDATA\nprintf '\\xe4\\xb8\\xad\\xe6\\x96\\x87\\n'\nexit 17"
                result = await client.execute("agent", script, timeout=10)
                self.assertEqual(result.exit_code, 17, result.output)
                self.assertEqual(result.output, "$HOME 'quotes' $(false)\n中文")
                again = await client.execute("agent", "printf ready", timeout=3)
                self.assertEqual(again.output, "ready")
                self.assertEqual(again.exit_code, 0)
        finally:
            loop.remove_reader(master)
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            os.close(master)


if __name__ == "__main__":
    unittest.main()
