import asyncio
import base64
from contextlib import suppress
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from local_proxy import Proxy, close, normalize_host, parse_http
import tunnel

FIXTURES = Path(__file__).parent / "fixtures"


class ConfigTests(unittest.TestCase):
    def test_domain_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "domains.txt"
            for bad in ("", "*.openai.com", "127.0.0.1", "openai.com:443", "bad..com", "-bad.com", "bad-.com", "https://openai.com"):
                path.write_text(bad)
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    tunnel.read_domains(path)
            path.write_text("# comment\nOpenAI.com\nopenai.com # duplicate\nclaude.ai\n")
            self.assertEqual(tunnel.read_domains(path), ["openai.com", "claude.ai"])

    def test_proxy_target_parser(self):
        self.assertEqual(parse_http(b"CONNECT API.OpenAI.com.:443 HTTP/1.1\r\n\r\n")[:2], ("api.openai.com", 443))
        for target in ("user:pass@openai.com:443", "openai.com:0", "openai.com:443/path", "openai.com:70000", "openai.com:443?x=1"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                parse_http(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
        host, port, body = parse_http(b"GET http://example.com/a?b=c HTTP/1.1\r\nHost: example.com\r\nProxy-Authorization: secret\r\n\r\n")
        self.assertEqual((host, port), ("example.com", 80))
        self.assertIn(b"GET /a?b=c HTTP/1.1", body)
        self.assertNotIn(b"secret", body)

    def test_remote_auth_and_restricted_ports(self):
        config = tunnel.remote_config(["openai.com"], "secret")
        self.assertEqual(config["services"][0]["handler"]["auth"]["password"], "secret")
        self.assertEqual(config["bypasses"][0]["matchers"], [".openai.com:80", ".openai.com:443"])
        self.assertTrue(config["bypasses"][0]["whitelist"])


class LifecycleTests(unittest.TestCase):
    def test_readiness_failure_terminates_sandbox_and_removes_state(self):
        sandbox = MagicMock(object_id="sb-test")
        sandbox.wait_until_ready.side_effect = RuntimeError("not ready")
        sdk = MagicMock()
        sdk.Sandbox.create.return_value = sandbox
        args = SimpleNamespace(domains=tunnel.ROOT / "domains.txt", port=0, duration=60, smoke=False)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "runtime" / "session.json"
            with patch.dict("sys.modules", {"modal": sdk}), patch.object(tunnel, "STATE", state), \
                    patch.object(tunnel, "gost_binary", return_value=Path("gost")):
                with self.assertRaisesRegex(RuntimeError, "not ready"):
                    tunnel.start(args)
            sandbox.terminate.assert_called_once_with(wait=True)
            self.assertFalse(state.exists())
            self.assertEqual(list(state.parent.iterdir()), [])


class DeploymentTests(unittest.TestCase):
    def test_remote_import_does_not_download_or_build_image(self):
        import runpy
        sdk = MagicMock()
        sdk.is_local.return_value = False
        with patch.dict("sys.modules", {"modal": sdk}), \
                patch.dict("os.environ", {"TUNNEL_SECRET_NAME": "test-secret"}), \
                patch.object(tunnel, "gost_binary", side_effect=AssertionError("remote download")):
            runpy.run_path(str(tunnel.ROOT / "modal_app.py"))
        self.assertEqual(sdk.App.return_value.function.call_args.kwargs["env"],
                         {"TUNNEL_SECRET_NAME": "test-secret"})
        sdk.Image.debian_slim.assert_not_called()


class LocalProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tasks = set()
        self.origin_hits = 0
        self.remote_targets = []
        self.remote_status = 200
        self.origin = await asyncio.start_server(self.origin_handler, "127.0.0.1", 0)
        self.origin_port = self.origin.sockets[0].getsockname()[1]
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(FIXTURES / "localhost.pem", FIXTURES / "localhost-key.pem")
        self.remote = await asyncio.start_server(self.remote_handler, "127.0.0.1", 0, ssl=tls)
        config = tunnel.local_config(["localhost", "openai.com"], "secret", "localhost", self.remote.sockets[0].getsockname()[1], 0)
        self.proxy = Proxy(config)
        self.proxy.tls.load_verify_locations(FIXTURES / "localhost.pem")
        self.local = await asyncio.start_server(self.proxy.handle, "127.0.0.1", 0)
        self.port = self.local.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for server in (self.local, self.remote, self.origin):
            server.close()
            await server.wait_closed()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def origin_handler(self, reader, writer):
        task = asyncio.current_task(); self.tasks.add(task)
        try:
            await reader.readuntil(b"\r\n\r\n")
            self.origin_hits += 1
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
        finally:
            await close(writer)
            self.tasks.discard(task)

    async def remote_handler(self, reader, writer):
        task = asyncio.current_task(); self.tasks.add(task)
        upstream = None
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            self.remote_targets.append(header.split(b"\r\n")[0])
            expected = base64.b64encode(b"tunnel:secret")
            status = self.remote_status if b"Proxy-Authorization: Basic " + expected in header else 407
            if status != 200:
                writer.write(f"HTTP/1.1 {status} Rejected\r\nContent-Length: 0\r\n\r\n".encode())
                await writer.drain()
                return
            other, upstream = await asyncio.open_connection("127.0.0.1", self.origin_port)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await Proxy.relay(reader, writer, other, upstream)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            if upstream is not None:
                await close(upstream)
            await close(writer)
            self.tasks.discard(task)

    async def connect(self, target):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        await writer.drain()
        header = await reader.readuntil(b"\r\n\r\n")
        return reader, writer, header

    async def fetch(self, reader, writer):
        writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 3)
        self.assertTrue(response.endswith(b"ok"), response)
        await close(writer)

    async def test_http_connect_uses_verified_tls_and_remote_dns(self):
        with patch("socket.getaddrinfo", wraps=__import__("socket").getaddrinfo) as dns:
            reader, writer, header = await self.connect("api.openai.com:443")
            self.assertIn(b"200", header)
            await self.fetch(reader, writer)
            self.assertFalse(any(call.args[0] == "api.openai.com" for call in dns.call_args_list))
        self.assertTrue(any(target.startswith(b"CONNECT api.openai.com:443 ") for target in self.remote_targets))

    async def test_plain_http_strips_proxy_auth_and_uses_direct_destination(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(f"GET http://127.0.0.1:{self.origin_port}/ HTTP/1.1\r\nHost: localhost\r\nProxy-Authorization: secret\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 3)
        self.assertTrue(response.endswith(b"ok"))
        self.assertEqual(self.remote_targets, [])
        await close(writer)

    async def test_client_half_close_keeps_download_alive(self):
        reader, writer, header = await self.connect(f"127.0.0.1:{self.origin_port}")
        self.assertIn(b"200", header)
        writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.write_eof()
        self.assertTrue((await asyncio.wait_for(reader.read(), 3)).endswith(b"ok"))
        await close(writer)

    async def test_socks5_domain_mode(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"\x05\x01\x00"); await writer.drain()
        self.assertEqual(await reader.readexactly(2), b"\x05\x00")
        host = b"api.openai.com"
        writer.write(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + b"\x01\xbb")
        await writer.drain()
        self.assertEqual((await reader.readexactly(10))[:2], b"\x05\x00")
        await self.fetch(reader, writer)

    async def test_selected_domain_does_not_fallback_after_rejection(self):
        self.remote_status = 403
        reader, writer, header = await self.connect(f"localhost:{self.origin_port}")
        self.assertIn(b"502", header)
        await close(writer)
        self.assertEqual(self.origin_hits, 0)
        reader, writer, header = await self.connect(f"127.0.0.1:{self.origin_port}")
        self.assertIn(b"200", header)
        await self.fetch(reader, writer)
        self.assertEqual(self.origin_hits, 1)

    async def test_certificate_mismatch_fails_closed(self):
        self.proxy.config["hostname"] = "127.0.0.1"
        reader, writer, header = await self.connect(f"localhost:{self.origin_port}")
        self.assertIn(b"502", header)
        await close(writer)
        self.assertEqual(self.origin_hits, 0)

    async def test_wrong_remote_password_fails_closed(self):
        self.proxy.config["password"] = "wrong"
        reader, writer, header = await self.connect(f"localhost:{self.origin_port}")
        self.assertIn(b"502", header)
        await close(writer)
        self.assertEqual(self.origin_hits, 0)

    async def test_suffix_matching_has_label_boundary(self):
        self.assertTrue(self.proxy.selected("api.openai.com"))
        self.assertTrue(self.proxy.selected("openai.com"))
        self.assertFalse(self.proxy.selected("evilopenai.com"))
        self.assertFalse(self.proxy.selected("openai.com.attacker.invalid"))


class WebSocketProxyTests(LocalProxyTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from websockets.asyncio.server import serve
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(FIXTURES / "localhost.pem", FIXTURES / "localhost-key.pem")
        self.remote.close()
        await self.remote.wait_closed()
        self.remote = await serve(self.ws_handler, "127.0.0.1", 0, ssl=tls, compression=None)
        self.proxy.config["url"] = f"wss://localhost:{self.remote.sockets[0].getsockname()[1]}/ws"

    async def ws_handler(self, websocket):
        from ws_stream import WebSocketStream
        stream = WebSocketStream(websocket)
        await self.remote_handler(stream, stream)

    async def test_certificate_mismatch_fails_closed(self):
        self.proxy.config["url"] = self.proxy.config["url"].replace("localhost", "127.0.0.1")
        reader, writer, header = await self.connect(f"localhost:{self.origin_port}")
        self.assertIn(b"502", header)
        await close(writer)
        self.assertEqual(self.origin_hits, 0)

    async def test_fragmented_connect_response_preserves_payload(self):
        from ws_stream import WebSocketStream
        ws = MagicMock()
        from unittest.mock import AsyncMock
        ws.recv = AsyncMock(side_effect=[b"HTTP/1.1 200 OK\r\n", b"\r\npayload"])
        stream = WebSocketStream(ws)
        self.assertEqual(await stream.readuntil(b"\r\n\r\n"), b"HTTP/1.1 200 OK\r\n\r\n")
        self.assertEqual(await stream.read(1024), b"payload")


if __name__ == "__main__":
    unittest.main()
