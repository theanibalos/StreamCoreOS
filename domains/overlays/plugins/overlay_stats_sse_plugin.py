import asyncio
import json
from core.base_plugin import BasePlugin


class OverlayStatsSsePlugin(BasePlugin):
    """
    GET /overlays/stats  (SSE, public — used by OBS browser source)

    Pushes stat updates in real-time instead of polling:
      subscribers.active_total  — pushed on each sub/unsub event
      followers.total           — pushed on each follow event (also corrected every 5min)
      stream.viewer_count       — pushed every 5min from channel_stats collector
      stream.online             — pushed when stream goes on/off
      bits.total                — pushed on each cheer event

    On connect: current values are sent immediately so the widget populates at once.
    """

    def __init__(self, http, db, state, event_bus, twitch, logger):
        self.http = http
        self.db = db
        self.state = state
        self.bus = event_bus
        self.twitch = twitch
        self.logger = logger
        self._queues: list[asyncio.Queue] = []
        self._follower_count: int = 0

    async def on_boot(self):
        # Seed follower count from last collected snapshot
        try:
            row = await self.db.query_one(
                "SELECT follower_count FROM channel_stats ORDER BY id DESC LIMIT 1", []
            )
            if row:
                self._follower_count = int(row["follower_count"])
        except Exception:
            pass

        # Subscriber changes
        await self.bus.subscribe("subscriber.new",     self._on_push)
        await self.bus.subscribe("subscriber.expired", self._on_push)
        await self.bus.subscribe("subscriber.resub",   self._on_push)
        await self.bus.subscribe("subscriber.gift",    self._on_push)

        # Stream on/off
        await self.bus.subscribe("stream.session.started", self._on_push)
        await self.bus.subscribe("stream.session.ended",   self._on_push)

        # Stats collector runs every 5 min — use it to update viewer + authoritative follower
        await self.bus.subscribe("dashboard.stats.updated", self._on_stats_updated)

        # Follow events — real-time follower count increment
        self.twitch.on_event("channel.follow", self._on_follow)

        # Bits events — real-time bits total update
        self.twitch.on_event("channel.cheer", self._on_push_event)

        # Overlay config saved — notify live pages to reload
        await self.bus.subscribe("overlay.config.updated", self._on_config_updated)

        self.http.add_sse_endpoint(
            "/api/overlays/stats",
            self._stream,
            tags=["Overlays"],
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _current_stats(self) -> dict:
        stats: dict = {
            "stream.online":   self.state.get("online", default=False, namespace="stream_state"),
            "followers.total": self._follower_count,
        }
        try:
            row = await self.db.query_one(
                "SELECT COUNT(*) AS n FROM subscribers WHERE is_active=1", []
            )
            stats["subscribers.active_total"] = int(row["n"]) if row else 0
        except Exception:
            stats["subscribers.active_total"] = 0

        try:
            row = await self.db.query_one(
                "SELECT viewer_count FROM channel_stats ORDER BY id DESC LIMIT 1", []
            )
            stats["stream.viewer_count"] = int(row["viewer_count"]) if row else 0
        except Exception:
            stats["stream.viewer_count"] = 0

        try:
            row = await self.db.query_one(
                "SELECT COALESCE(SUM(bits_total), 0) AS n FROM viewer_bits", []
            )
            stats["bits.total"] = int(row["n"]) if row else 0
        except Exception:
            stats["bits.total"] = 0

        return stats

    async def _broadcast(self, stats: dict):
        if not self._queues:
            return
        msg = json.dumps(stats)
        for q in self._queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_push(self, _data: dict):
        await self._broadcast(await self._current_stats())

    async def _on_push_event(self, _event: dict):
        await self._broadcast(await self._current_stats())

    async def _on_follow(self, _event: dict):
        self._follower_count += 1
        await self._broadcast(await self._current_stats())

    async def _on_config_updated(self, data: dict):
        overlay_id = data.get("overlay_id")
        try:
            # Ensure overlay_id is int for consistent DB lookups
            oid = int(overlay_id) if overlay_id is not None else None
            row = await self.db.query_one(
                "SELECT config FROM overlays WHERE id = $1", [oid]
            )
            config = json.loads(row["config"]) if row else {}
            self.logger.info(f"[OverlayStatsSSE] Config updated for overlay {oid}")
        except Exception as e:
            self.logger.error(f"[OverlayStatsSSE] Error reloading config for {overlay_id}: {e}")
            config = {}
        
        # We push BOTH the new config and the current stats so the frontend
        # can map them to new element IDs instantly without flickering to "..."
        stats = await self._current_stats()
        await self._broadcast({
            "__type": "config_updated", 
            "overlay_id": overlay_id, 
            "config": config,
            "stats": stats
        })

    async def _on_stats_updated(self, data: dict):
        # Stats collector provides authoritative follower count from Twitch API
        if "follower_count" in data:
            self._follower_count = int(data["follower_count"])
        await self._broadcast(await self._current_stats())

    # ── SSE stream ────────────────────────────────────────────────────────────

    async def _stream(self, data: dict):
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._queues.append(queue)
        try:
            # Send current stats immediately on connect
            yield f"data: {json.dumps(await self._current_stats())}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat to keep OBS browser source connection alive
                    yield ": ping\n\n"
        finally:
            self._queues.remove(queue)
