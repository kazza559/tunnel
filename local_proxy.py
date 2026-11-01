"""Loopback-only HTTP CONNECT / SOCKS5 frontend; destination TLS stays end to end."""
import asyncio
import base64
import contextlib
import ipaddress
import json
from pathlib import Path
import ssl
import sys
from urllib.parse import urlsplit


async def close(writer):
    writer.close()
    with contextlib.suppress(OSError, asyncio.TimeoutError):
        await asyncio.wait_for(writer.wait_closed(), 5)


def normalize_host(host):
    if not host or any(c.isspace() or c in "/\\@?#%" or ord(c) < 33 for c in host):
        raise ValueError("Invalid target hostname")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        if len(host) > 253 or any(not label or len(label) > 63
                or label.startswith("-") or label.endswith("-")
                or not all(c.isalnum() or c == "-" for c in label)
                for label in host.split(".")):
            raise ValueError("Invalid target hostname")
        return host


def authority(host, port):
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def parse_http(header):
    lines = header.decode("iso-8859-1").split("\r\n")
    method, target, version = lines[0].split(" ")
    if version not in ("HTTP/1.0", "HTTP/1.1"):
        raise ValueError("Unsupported HTTP version")
    url = urlsplit("//" + target if method == "CONNECT" else target)
    if url.username is not None or url.password is not None or url.fragment:
        raise ValueError("Invalid proxy target")
    if method == "CONNECT":
        if url.path or url.query or url.port is None:
            raise ValueError("CONNECT requires host:port")
        body = b""
    else:
        if url.scheme != "http" or not method.isalpha():
            raise ValueError("Use CONNECT for HTTPS")
        headers = []
        for line in lines[1:]:
            if not line:
                continue
            key, _ = line.split(":", 1)
            if key.lower() not in ("proxy-authorization", "proxy-connection"):
                headers.append(line)
        path = (url.path or "/") + ("?" + url.query if url.query else "")
        body = (f"{method} {path} {version}\r\n" + "\r\n".join(headers) + "\r\n\r\n").encode("iso-8859-1")
    host = normalize_host(url.hostname)
    port = url.port if url.port is not None else 80
    if not 1 <= port <= 65535:
        raise ValueError("Invalid target port")
    return host, port, body


class Proxy:
    def __init__(self, config):
        self.config = config
        self.tls = ssl.create_default_context()

    def selected(self, host):
        return any(host == domain or host.endswith("." + domain) for domain in self.config["domains"])

    async def connect(self, host, port):
        if not self.selected(host):
            return await asyncio.open_connection(host, port, limit=65536)
        if "ssh_port" in self.config:
            if not self.config.get("ssh_ready"):
                raise OSError("SSH tunnel is not ready")
            reader, writer = await asyncio.open_connection("127.0.0.1", self.config["ssh_port"], limit=65536)
        elif self.config.get("url"):
            from websockets.asyncio.client import connect
            from websockets.exceptions import WebSocketException
            from ws_stream import WebSocketStream
            try:
                websocket = await connect(self.config["url"], ssl=self.tls, proxy=None,
                                          max_size=2**21, compression=None, open_timeout=30)
            except WebSocketException:
                raise OSError("Cannot open deployed Modal tunnel") from None
            reader = writer = WebSocketStream(websocket)
        else:
            reader, writer = await asyncio.open_connection(
                self.config["hostname"], self.config["port"], ssl=self.tls,
                server_hostname=self.config["hostname"], limit=65536,
            )
        try:
            authentication = ""
            if "ssh_port" not in self.config:
                token = base64.b64encode(("tunnel:" + self.config["password"]).encode()).decode()
                authentication = f"Proxy-Authorization: Basic {token}\r\n"
            target = authority(host, port)
            writer.write((f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
                          f"{authentication}\r\n").encode())
            await writer.drain()
            header = await reader.readuntil(b"\r\n\r\n")
            status = header.split(b"\r\n", 1)[0].split()
            if len(status) < 2 or status[1] != b"200":
                raise OSError("Modal proxy rejected CONNECT")
            if self.config.get("trace"):
                print(f"MODAL {target}", flush=True)
            return reader, writer
        except BaseException:
            await close(writer)
            raise

    async def handle(self, reader, writer):
        upstream = None
        established = False
        socks = False
        try:
            first = await asyncio.wait_for(reader.readexactly(1), 10)
            socks = first == b"\x05"
            async with asyncio.timeout(45):
                if socks:
                    count = (await reader.readexactly(1))[0]
                    methods = await reader.readexactly(count)
                    if 0 not in methods:
                        writer.write(b"\x05\xff")
                        await writer.drain()
                        return
                    writer.write(b"\x05\x00")
                    await writer.drain()
                    version, command, reserved, address_type = await reader.readexactly(4)
                    if (version, command, reserved) != (5, 1, 0):
                        raise ValueError("Only SOCKS5 CONNECT is supported")
                    if address_type == 3:
                        length = (await reader.readexactly(1))[0]
                        host = (await reader.readexactly(length)).decode("ascii")
                    elif address_type in (1, 4):
                        size = 4 if address_type == 1 else 16
                        host = str(ipaddress.ip_address(await reader.readexactly(size)))
                    else:
                        raise ValueError("Invalid SOCKS address type")
                    host = normalize_host(host)
                    port = int.from_bytes(await reader.readexactly(2), "big")
                    if port == 0:
                        raise ValueError("Invalid target port")
                    initial = b""
                else:
                    header = first + await reader.readuntil(b"\r\n\r\n")
                    host, port, initial = parse_http(header)
                remote_reader, upstream = await self.connect(host, port)
                if socks:
                    writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
                elif not initial:
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                if initial:
                    upstream.write(initial)
                    await upstream.drain()
                established = True
            await self.relay(reader, writer, remote_reader, upstream)
        except (OSError, ValueError, UnicodeError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError, asyncio.TimeoutError):
            if not established:
                writer.write(b"\x05\x01\x00\x01" + b"\x00" * 6 if socks else
                             b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                with contextlib.suppress(OSError):
                    await writer.drain()
        finally:
            if upstream is not None:
                await close(upstream)
            await close(writer)

    @staticmethod
    async def relay(client_reader, client_writer, remote_reader, remote_writer):
        async def copy(reader, writer):
            while data := await asyncio.wait_for(reader.read(65536), 300):
                writer.write(data)
                await writer.drain()
            if writer.can_write_eof():
                writer.write_eof()
        tasks = [asyncio.create_task(copy(client_reader, remote_writer)),
                 asyncio.create_task(copy(remote_reader, client_writer))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if tasks[0].done():
                await tasks[0]
            await tasks[1]
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def serve(config):
    proxy = Proxy(config)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", config["local_port"], limit=65536)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(serve(json.loads(Path(sys.argv[1]).read_text())))
    except KeyboardInterrupt:
        pass
