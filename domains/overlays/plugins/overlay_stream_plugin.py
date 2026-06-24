import asyncio
import json
from core.base_plugin import BasePlugin


class OverlayStreamPlugin(BasePlugin):
    """
    GET /api/overlays/stream/{id}  (SSE — public, used by OBS browser source)

    Single multiplexed SSE stream per overlay. Replaces the 3 separate SSE
    connections the live overlay page used to open.

    On connect: reads the overlay config from DB to determine which event
    types are needed. Only enqueues relevant messages — a chat-only overlay
    never receives stats or alert messages.

    Message types:
      {"type": "stats",          "data": { stat_key: value, ... }}
      {"type": "chat",           "data": { display_name, message, ... }}
      {"type": "alert",          "data": { "type": event_type, "data": {...} }}
      {"type": "config_updated", "data": { overlay_id, config, stats }}
      {"__type": "heartbeat"}    — filtered client-side by the SSE watchdog
    """

    def __init__(self, http, db, state, event_bus, twitch, logger):
        self.http = http
        self.db = db
        self.state = state
        self.bus = event_bus
        self.twitch = twitch
        self.logger = logger
        self._follower_count: int = 0
        # overlay_id (str) → {"needs_stats", "needs_chat", "needs_alerts", "queues"}
        self._registry: dict[str, dict] = {}

    async def on_boot(self):
        try:
            row = await self.db.query_one(
                "SELECT follower_count FROM channel_stats ORDER BY id DESC LIMIT 1", []
            )
            if row:
                self._follower_count = int(row["follower_count"])
        except Exception:
            pass

        await self.bus.subscribe("chat.message.received",   self._on_chat)
        await self.bus.subscribe("dashboard.stats.updated", self._on_stats_updated)
        await self.bus.subscribe("overlay.config.updated",  self._on_config_updated)

        # All Twitch events — alerts go straight to the overlay unprocessed.
        # Stats-relevant events (follows, subs, etc.) are handled via the
        # dashboard.stats.updated bus event published by the stats collector.
        self.twitch.on_event("*", self._on_twitch_event)

        self.http.add_sse_endpoint(
            "/api/overlays/stream/{id}",
            self._stream,
            tags=["Overlays"],
        )

    # ── Needs detection ───────────────────────────────────────────────

    def _detect_needs(self, elements: list) -> dict:
        needs = {"needs_stats": False, "needs_chat": False, "needs_alerts": False}
        for el in elements:
            t = el.get("type")
            if t in ("stat", "progress_bar"):
                needs["needs_stats"] = True
            elif t in ("chat_highlight", "custom_code"):
                needs["needs_chat"] = True
            elif t == "alert":
                needs["needs_alerts"] = True
        return needs

    # ── Broadcast helpers ─────────────────────────────────────────────

    def _enqueue(self, queue: asyncio.Queue, msg: str):
        try:
            queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    def _broadcast_by_need(self, need_key: str, message: dict):
        msg = json.dumps(message)
        for entry in self._registry.values():
            if entry.get(need_key):
                for q in entry["queues"]:
                    self._enqueue(q, msg)

    def _broadcast_to_overlay(self, overlay_id: str, message: dict):
        entry = self._registry.get(overlay_id)
        if not entry:
            return
        msg = json.dumps(message)
        for q in entry["queues"]:
            self._enqueue(q, msg)

    # ── Event handlers ────────────────────────────────────────────────

    async def _on_stats_updated(self, data: dict):
        if "follower_count" in data:
            self._follower_count = int(data["follower_count"])
        self._broadcast_by_need("needs_stats", {
            "type": "stats", "data": await self._current_stats()
        })

    async def _on_chat(self, data: dict):
        self._broadcast_by_need("needs_chat", {"type": "chat", "data": data})

    async def _on_twitch_event(self, event_data: dict):
        event_type = event_data.get("_event_type", "twitch.event")
        payload = {k: v for k, v in event_data.items() if k != "_event_type"}

        # Stats-relevant events: push a fresh snapshot to stat overlays
        if event_type in ("channel.follow", "channel.subscribe",
                          "channel.subscription.gift", "channel.cheer",
                          "stream.online", "stream.offline"):
            if event_type == "channel.follow":
                self._follower_count += 1
            self._broadcast_by_need("needs_stats", {
                "type": "stats", "data": await self._current_stats()
            })

        # All events go to alert overlays
        self._broadcast_by_need("needs_alerts", {
            "type": "alert", "data": {"type": event_type, "data": payload}
        })

    async def _on_config_updated(self, data: dict):
        overlay_id = str(data.get("overlay_id", ""))
        entry = self._registry.get(overlay_id)
        if not entry:
            return
        try:
            row = await self.db.query_one(
                "SELECT config FROM overlays WHERE id = $1", [int(overlay_id)]
            )
            config = json.loads(row["config"]) if row else {}
        except Exception as e:
            self.logger.error(f"[OverlayStream] Config reload error: {e}")
            config = {}

        entry.update(self._detect_needs(config.get("elements", [])))
        self._broadcast_to_overlay(overlay_id, {
            "type": "config_updated",
            "data": {"overlay_id": overlay_id, "config": config, "stats": await self._current_stats()}
        })

    # ── SSE stream ────────────────────────────────────────────────────

    async def _stream(self, data: dict):
        overlay_id = str(data.get("id", ""))
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        try:
            row = await self.db.query_one(
                "SELECT config FROM overlays WHERE id = $1", [int(overlay_id)]
            )
            config = json.loads(row["config"]) if row else {}
        except Exception:
            config = {}

        needs = self._detect_needs(config.get("elements", []))

        if overlay_id not in self._registry:
            self._registry[overlay_id] = {**needs, "queues": []}
        else:
            self._registry[overlay_id].update(needs)

        self._registry[overlay_id]["queues"].append(queue)

        try:
            if needs["needs_stats"]:
                yield f"data: {json.dumps({'type': 'stats', 'data': await self._current_stats()})}\n\n"
            else:
                yield 'data: {"__type":"heartbeat"}\n\n'
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield 'data: {"__type":"heartbeat"}\n\n'
        finally:
            self._registry[overlay_id]["queues"].remove(queue)
            if not self._registry[overlay_id]["queues"]:
                del self._registry[overlay_id]

    # ── Stats snapshot ────────────────────────────────────────────────

    async def _current_stats(self) -> dict:
        stats: dict = {
            "stream.online":   self.state.get("online", default=False, namespace="stream_state"),
            "followers.total": self._follower_count,
        }
        try:
            row = await self.db.query_one(
                "SELECT viewer_count FROM channel_stats ORDER BY id DESC LIMIT 1", []
            )
            stats["stream.viewer_count"] = int(row["viewer_count"]) if row else 0
        except Exception:
            stats["stream.viewer_count"] = 0

        try:
            row = await self.db.query_one(
                "SELECT COUNT(*) AS n FROM subscribers WHERE is_active=1", []
            )
            stats["subscribers.active_total"] = int(row["n"]) if row else 0
        except Exception:
            stats["subscribers.active_total"] = 0

        try:
            row = await self.db.query_one(
                "SELECT COALESCE(SUM(bits_total), 0) AS n FROM viewer_bits", []
            )
            stats["bits.total"] = int(row["n"]) if row else 0
        except Exception:
            stats["bits.total"] = 0

        return stats
