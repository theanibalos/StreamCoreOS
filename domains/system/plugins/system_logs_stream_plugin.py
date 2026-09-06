import asyncio
import json
from microcoreos.base_plugin import BasePlugin


class SystemLogsStreamPlugin(BasePlugin):
    """
    Streams every log entry in real time via Server-Sent Events.
    Connect to GET /system/logs/stream to receive live log records.
    """

    def __init__(self, http, logger):
        self.http = http
        self.logger = logger
        self._queues: set = set()

    async def on_boot(self):
        self.logger.add_sink(self._on_log)
        self.http.add_sse_endpoint(
            "/system/logs/stream",
            generator=self._stream,
            tags=["System"],
        )
        self.http.add_sse_endpoint(
            "/api/system/logs/stream",
            generator=self._stream,
            tags=["System"],
        )

    def _on_log(self, level: str, message: str, timestamp: str, identity: str, trace_id: str = None):
        if not self._queues:
            return
        record = {
            "level": level,
            "message": message,
            "timestamp": timestamp,
            "identity": identity,
            "trace_id": trace_id,
        }
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
