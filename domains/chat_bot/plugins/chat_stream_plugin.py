import asyncio
import json
from core.base_plugin import BasePlugin


class ChatStreamPlugin(BasePlugin):
    """
    GET /chat/stream  (SSE)

    Streams live chat messages to connected clients.
    Each message is a JSON-encoded chat.message.received payload.

    One subscriber is registered globally; per-connection queues fan out
    the events to each active SSE client.
    """

    def __init__(self, http, event_bus, logger):
        self.http = http
        self.bus = event_bus
        self.logger = logger
        self._queues: list[asyncio.Queue] = []

    async def on_boot(self):
        await self.bus.subscribe("chat.message.received", self._on_message)
        self.http.add_sse_endpoint(
            "/api/chat/stream",
            self._stream,
            tags=["Chat"],
        )

    async def _on_message(self, event):
        for queue in self._queues:
            try:
                queue.put_nowait(event.payload)
            except asyncio.QueueFull:
                pass  # slow client — drop the message

    async def _stream(self, data: dict):
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues.append(queue)
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield 'data: {"__type":"heartbeat"}\n\n'
        finally:
            self._queues.remove(queue)
