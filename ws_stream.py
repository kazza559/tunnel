"""Byte-stream adapter for GOST's binary WebSocket transport."""
import asyncio
from websockets.exceptions import ConnectionClosed, WebSocketException


class WebSocketStream:
    def __init__(self, websocket):
        self.websocket = websocket
        self.incoming = bytearray()
        self.outgoing = bytearray()
        self.close_task = None

    async def receive(self):
        try:
            data = await self.websocket.recv()
        except ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code in (1000, 1001):
                return False
            raise OSError("Tunnel WebSocket closed unexpectedly") from None
        except WebSocketException:
            raise OSError("Tunnel WebSocket failed") from None
        if not isinstance(data, bytes):
            raise OSError("Tunnel requires binary WebSocket messages")
        self.incoming.extend(data)
        return True

    async def read(self, size):
        while not self.incoming:
            if not await self.receive():
                return b""
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data

    async def readuntil(self, separator):
        while True:
            end = self.incoming.find(separator)
            if end >= 0:
                end += len(separator)
                data = bytes(self.incoming[:end])
                del self.incoming[:end]
                return data
            if len(self.incoming) > 65536:
                raise OSError("Tunnel response headers too large")
            if not await self.receive():
                raise OSError("Tunnel closed before CONNECT response")

    def write(self, data):
        self.outgoing.extend(data)

    async def drain(self):
        if self.outgoing:
            data = bytes(self.outgoing)
            self.outgoing.clear()
            try:
                await self.websocket.send(data)
            except WebSocketException:
                raise OSError("Tunnel WebSocket send failed") from None

    def can_write_eof(self):
        return False

    def close(self):
        if self.close_task is None:
            self.close_task = asyncio.create_task(self.websocket.close())

    async def wait_closed(self):
        if self.close_task is not None:
            await self.close_task
        await self.websocket.wait_closed()
