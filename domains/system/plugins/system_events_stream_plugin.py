import asyncio
import json
from microcoreos.base_plugin import BasePlugin


class SystemEventsStreamPlugin(BasePlugin):
    """
    Streams every event bus event in real time via Server-Sent Events.
    Connect to GET /system/events/stream to receive live event records.
    """

    def __init__(self, http, event_bus):
        self.http = http
        self.event_bus = event_bus
        self._queues: set = set()

    async def on_boot(self):
        self.event_bus.add_listener(self._on_event)
        self.http.add_sse_endpoint(
            "/api/system/events/stream",
            generator=self._stream,
            tags=["System"],
        )

    def _on_event(self, record: dict):
        if not self._queues:
            return
        try:
            loop = asyncio.get_running_loop()
            for q in list(self._queues):
                try:
                    loop.call_soon_threadsafe(q.put_nowait, record)
                except asyncio.QueueFull:
                    pass  # slow consumer — drop event rather than grow unbounded
        except RuntimeError:
            pass

    async def _stream(self, data: dict):
        queue = asyncio.Queue(maxsize=200)
        self._queues.add(queue)
        try:
            while True:
                record = await queue.get()
                yield f"data: {json.dumps(record)}\n\n"
        finally:
            self._queues.discard(queue)
