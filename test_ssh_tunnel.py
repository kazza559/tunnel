import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from local_proxy import Proxy, close
import ssh_tunnel


class DiscoveryTests(unittest.TestCase):
    def test_endpoint_is_rediscovered_without_changing_host_identity(self):
        sdk = MagicMock()
        class Missing(Exception):
            pass
        sdk.exception.NotFoundError = Missing
        sdk.exception.AlreadyExistsError = type("Exists", (Exception,), {})
        first = MagicMock(object_id="sb-first")
        second = MagicMock(object_id="sb-second")
        first.tunnels.return_value = {2222: MagicMock(tcp_socket=("first.modal.host", 12345))}
        second.tunnels.return_value = {2222: MagicMock(tcp_socket=("second.modal.host", 23456))}
        sdk.Sandbox.from_name.side_effect = [first, second]
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("sys.modules", {"modal": sdk}), \
                patch.object(ssh_tunnel, "sandbox_name", return_value="stable-name"), \
                patch.object(ssh_tunnel, "STATE", Path(directory) / "state.json"):
            self.assertEqual(ssh_tunnel.ensure_sandbox(["openai.com"]), ("first.modal.host", 12345))
            self.assertEqual(ssh_tunnel.ensure_sandbox(["openai.com"]), ("second.modal.host", 23456))
            self.assertIn("sb-second", ssh_tunnel.STATE.read_text())
        sdk.Sandbox.create.assert_not_called()
        self.assertEqual(sdk.Sandbox.from_name.call_count, 2)

    def test_command_requires_preprovisioned_host_key_and_dedicated_identity(self):
        command = ssh_tunnel.ssh_command("relay.modal.host", 23456, 32000)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("HostKeyAlias=tunnel-modal", command)
        self.assertIn("UpdateHostKeys=no", command)
        self.assertIn("IdentitiesOnly=yes", command)
        self.assertIn("ExitOnForwardFailure=yes", command)
        self.assertIn("127.0.0.1:32000:127.0.0.1:8080", command)
        self.assertEqual(command[-1], "tunnel@relay.modal.host")
        self.assertNotIn("StrictHostKeyChecking=no", command)
        self.assertNotIn("accept-new", " ".join(command))

    def test_server_revision_changes_discovery_name_without_replacing_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ssh_client_ed25519.pub").write_text("client public key")
            (root / "ssh_host_ed25519.pub").write_text("host public key")
            source = root / "ssh_server.py"
            source.write_text("server revision 1")
            with patch.object(ssh_tunnel, "RUNTIME", root), patch.object(ssh_tunnel, "ROOT", root):
                old = ssh_tunnel.sandbox_name(["openai.com"])
                self.assertEqual(old, ssh_tunnel.sandbox_name(["openai.com"]))
                source.write_text("server revision 2")
                self.assertNotEqual(old, ssh_tunnel.sandbox_name(["openai.com"]))
            self.assertEqual((root / "ssh_host_ed25519.pub").read_text(), "host public key")

    def test_fresh_sandbox_is_named_and_exposes_only_raw_ssh(self):
        sdk = MagicMock()
        class Missing(Exception):
            pass
        sdk.exception.NotFoundError = Missing
        sdk.exception.AlreadyExistsError = type("Exists", (Exception,), {})
        sdk.Sandbox.from_name.side_effect = Missing()
        sandbox = sdk.Sandbox.create.return_value
        sandbox.object_id = "sb-new"
        sandbox.tunnels.return_value = {2222: MagicMock(tcp_socket=("relay.modal.host", 23456))}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ssh_host_ed25519").write_text("test host key")
            (root / "ssh_client_ed25519.pub").write_text("test client public key")
            with patch.dict("sys.modules", {"modal": sdk}), \
                    patch.object(ssh_tunnel, "sandbox_name", return_value="stable-name"), \
                    patch.object(ssh_tunnel, "RUNTIME", root), \
                    patch.object(ssh_tunnel, "STATE", root / "state.json"), \
                    patch.object(ssh_tunnel, "gost_binary", return_value=root / "gost"):
                ssh_tunnel.ensure_sandbox(["openai.com"])
        config = sdk.Sandbox.create.call_args.kwargs
        self.assertEqual(config["name"], "stable-name")
        self.assertEqual(config["unencrypted_ports"], [2222])
        self.assertNotIn("encrypted_ports", config)
        self.assertEqual(config["timeout"], 86400)
        self.assertEqual(config["idle_timeout"], 300)
        secret = sdk.Secret.from_dict.call_args.args[0]
        import json
        proxy = json.loads(secret["GOST_CONFIG"])["services"][0]
        self.assertEqual(proxy["addr"], "127.0.0.1:8080")
        self.assertNotIn("auth", proxy["handler"])
        self.assertEqual(proxy["bypass"], "allowed")


class DefaultRoutingTests(unittest.TestCase):
    def test_reference_api_auth_and_first_party_hosts_use_ssh(self):
        domains = ssh_tunnel.read_domains(ssh_tunnel.ROOT / "domains.txt")
        proxy = Proxy({"domains": domains, "ssh_port": 12345, "ssh_ready": False})
        hosts = ("api.openai.com", "auth.openai.com", "chatgpt.com", "ab.chatgpt.com",
                 "persistent.oaistatic.com", "files.oaiusercontent.com", "events.oaistatsig.com",
                 "api.anthropic.com", "claude.ai", "downloads.claude.ai", "claude.com",
                 "platform.claude.com", "sso.claude.com", "mcp-proxy.anthropic.com",
                 "bridge.claudeusercontent.com", "widget.claudemcpcontent.com")
        for host in hosts:
            with self.subTest(host=host):
                self.assertTrue(proxy.selected(host))

    def test_shared_unrelated_and_diagnostic_hosts_remain_direct(self):
        domains = ssh_tunnel.read_domains(ssh_tunnel.ROOT / "domains.txt")
        proxy = Proxy({"domains": domains})
        for host in ("github.com", "raw.githubusercontent.com", "registry.npmjs.org",
                     "storage.googleapis.com", "example.com", "intranet.company.local",
                     "api.ipify.org", "evilopenai.com", "claude.ai.attacker.invalid", "127.0.0.1"):
            with self.subTest(host=host):
                self.assertFalse(proxy.selected(host))


class SshFrontendTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_ssh_fails_before_any_fallback(self):
        proxy = Proxy({"domains": ["openai.com"], "ssh_port": 12345, "ssh_ready": False})
        with patch("local_proxy.asyncio.open_connection") as dial:
            with self.assertRaisesRegex(OSError, "not ready"):
                await proxy.connect("api.openai.com", 443)
            dial.assert_not_called()

    async def test_connect_uses_private_forward_and_preserves_payload(self):
        headers = []
        async def upstream(reader, writer):
            headers.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\nhello")
            await writer.drain()
            await close(writer)
        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        try:
            proxy = Proxy({"domains": ["openai.com"], "ssh_port": server.sockets[0].getsockname()[1], "ssh_ready": True})
            reader, writer = await proxy.connect("api.openai.com", 443)
            self.assertEqual(await reader.readexactly(5), b"hello")
            self.assertIn(b"CONNECT api.openai.com:443", headers[0])
            self.assertNotIn(b"Proxy-Authorization", headers[0])
            await close(writer)
        finally:
            server.close()
            await server.wait_closed()

    async def test_lost_private_forward_does_not_dial_destination(self):
        proxy = Proxy({"domains": ["openai.com"], "ssh_port": 12345, "ssh_ready": True})
        from unittest.mock import AsyncMock
        with patch("local_proxy.asyncio.open_connection", new=AsyncMock(side_effect=OSError("SSH down"))) as dial:
            with self.assertRaises(OSError):
                await proxy.connect("api.openai.com", 443)
            dial.assert_awaited_once_with("127.0.0.1", 12345, limit=65536)


if __name__ == "__main__":
    unittest.main()
