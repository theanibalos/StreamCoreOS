import asyncio
import json
from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


# ── Modelos ───────────────────────────────────────────────────────────────────

class MetricRecord(BaseModel):
    tool: str
    method: str
    duration_ms: float
    success: bool
    timestamp: float

class SystemMetricsResponse(BaseModel):
    success: bool
    data: Optional[list[MetricRecord]] = None
    error: Optional[str] = None


# ── Plugin ────────────────────────────────────────────────────────────────────

class SystemMetricsPlugin(BasePlugin):
    """
    Exposes tool call metrics in two ways:
    1. GET /system/metrics        — last 1000 records (snapshot).
    2. GET /system/metrics/stream — SSE stream, one record per tool call.

    Each record: {tool, method, duration_ms, success, timestamp}
    duration_ms uses time.perf_counter() — microsecond precision.
    """

    def __init__(self, http, registry):
        self.http = http
        self.registry = registry
        self._queues: set = set()

    async def on_boot(self):
        self.registry.add_metrics_sink(self._on_metric)

        self.http.add_endpoint(
            "/api/system/metrics", "GET", self.get_metrics,
            tags=["System"],
            response_model=SystemMetricsResponse,
        )
        self.http.add_sse_endpoint(
            "/api/system/metrics/stream",
            generator=self._stream,
            tags=["System"],
        )

    def _on_metric(self, record: dict) -> None:
        if not self._queues:
            return
        try:
            loop = asyncio.get_running_loop()
            for q in list(self._queues):
                try:
                    loop.call_soon_threadsafe(q.put_nowait, record)
                except asyncio.QueueFull:
                    pass  # slow consumer — drop rather than grow unbounded
        except RuntimeError:
            pass

    async def get_metrics(self, data: dict, context=None):
        """Returns the last 1000 tool call records, newest first."""
        try:
            records = self.registry.get_metrics()
            records_sorted = sorted(records, key=lambda r: r["timestamp"], reverse=True)
            return {"success": True, "data": records_sorted}
        except Exception as e:
            print(f"[SystemMetrics] Error: {e}")
            return {"success": False, "error": "Could not retrieve metrics"}

    async def _stream(self, data: dict):
        queue = asyncio.Queue(maxsize=200)
        self._queues.add(queue)
        try:
            while True:
                record = await queue.get()
                yield f"data: {json.dumps(record)}\n\n"
        finally:
            self._queues.discard(queue)
