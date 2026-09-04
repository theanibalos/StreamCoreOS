import asyncio
import json
from microcoreos.base_plugin import BasePlugin

# Raw Twitch EventSub types that reach every plugin's wildcard listener but
# should NOT be forwarded to alert-widget overlays: channel.chat.message
# fires on every single chat line (would spam an "alert" widget on every
# message), and stream.online/offline are already covered as proper alerts
# by the "stream.session.started/ended" bus event forwarded below.
# Twin list: domains/dashboard/plugins/dashboard_alerts_plugin.py — keep both
# in sync, they intentionally filter the same wildcard firehose the same way.
_EXCLUDED_TWITCH_EVENTS = {
    "channel.chat.message",
    "stream.online",
    "stream.offline",
}


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

    Dynamic vars pool:
      Any plugin can push arbitrary variables to every overlay by publishing
      the bus event "overlay.vars.set" with a flat dict payload, e.g.:
          await self.bus.publish("overlay.vars.set", {"my.custom_var": 42})
      Vars are persisted in the overlay_vars table (survive restarts), merged
      into the stats snapshot, and broadcast to overlays as a "stats" message.
      Overlays read them as data.stats["my.custom_var"].
    """

    def __init__(self, http, db, state, event_bus, twitch, logger):
        self.http = http
        self.db = db
        self.state = state
        self.bus = event_bus
        self.twitch = twitch
        self.logger = logger
        self._follower_count: int = 0
        # Dynamic vars pool: merged into stats snapshots, persisted in DB.
        self._vars: dict = {}
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

        try:
            rows = await self.db.query("SELECT key, value FROM overlay_vars", [])
            for r in rows:
                try:
                    self._vars[r["key"]] = json.loads(r["value"])
                except (json.JSONDecodeError, TypeError):
                    self._vars[r["key"]] = r["value"]
        except Exception as e:
            self.logger.error(f"[OverlayStream] Vars pool load error: {e}")

        await self.bus.subscribe("chat.message.received",   self._on_chat)
        await self.bus.subscribe("dashboard.stats.updated", self._on_stats_updated)
        await self.bus.subscribe("overlay.config.updated",  self._on_config_updated)
        await self.bus.subscribe("overlay.vars.set",        self._on_vars_set)

        # System (non-Twitch) bus events forwarded to overlays as alerts.
        # Kept in sync with dashboard_alerts_plugin.py's _BUS_EVENTS — same
        # filtering intent, viewer.points.awarded excluded on purpose (fires
        # on every chat message, would spam an alert widget).
        for bus_event in ("stream.session.started", "stream.session.ended",
                          "viewer.regular.added", "viewer.regular.removed",
                          "moderation.action.taken", "chat.message.deleted",
                          "chat.command.received"):
            await self.bus.subscribe(bus_event, self._make_system_forwarder(bus_event))

        # Monetization & YouTube events forwarded as alerts
        await self.bus.subscribe("monetization.event.received",     self._on_monetization_event)
        await self.bus.subscribe("youtube.superchat.received",       self._on_youtube_superchat)
        await self.bus.subscribe("youtube.supersticker.received",    self._on_youtube_supersticker)
        await self.bus.subscribe("overlay.alert.trigger",            self._on_custom_alert_trigger)

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

    def _resolve_needs(self, config: dict) -> dict:
        """
        Reads the `needs` summary the frontend saves alongside the config
        (derived from each widget's declared data needs — see
        overlays/dataSource.svelte.ts computeOverlayNeeds). Overlays without
        the field receive no channels — re-save them in the builder.
        """
        explicit = config.get("needs") or {}
        return {
            "needs_stats": bool(explicit.get("stats", False)),
            "needs_chat": bool(explicit.get("chat", False)),
            "needs_alerts": bool(explicit.get("alerts", False)),
        }

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

    # ── Dynamic vars pool ─────────────────────────────────────────────

    async def _on_vars_set(self, event):
        data = event.payload
        if not isinstance(data, dict) or not data:
            return
        await self._set_vars(data)

    async def _set_vars(self, new_vars: dict):
        """Merge vars into the pool, persist them and broadcast the delta."""
        self._vars.update(new_vars)
        for key, value in new_vars.items():
            try:
                await self.db.execute(
                    "INSERT INTO overlay_vars (key, value, updated_at) "
                    "VALUES ($1, $2, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                    [str(key), json.dumps(value)],
                )
            except Exception as e:
                self.logger.error(f"[OverlayStream] Var persist error ({key}): {e}")
        self._broadcast_by_need("needs_stats", {"type": "stats", "data": new_vars})

    def _make_system_forwarder(self, event_type: str):
        async def _forward(event):
            self._broadcast_by_need("needs_alerts", {
                "type": "alert", "data": {"type": event_type, "data": event.payload or {}}
            })
        return _forward

    # ── Event handlers ────────────────────────────────────────────────

    async def _on_stats_updated(self, event):
        data = event.payload
        if "follower_count" in data:
            self._follower_count = int(data["follower_count"])
        self._broadcast_by_need("needs_stats", {
            "type": "stats", "data": await self._current_stats()
        })

    async def _on_chat(self, event):
        self._broadcast_by_need("needs_chat", {"type": "chat", "data": event.payload})

    async def _on_monetization_event(self, event):
        payload = event.payload or {}
        platform = payload.get("platform", "youtube")
        mtype = payload.get("type", "monetization")
        event_type = f"{platform}.{mtype}" if platform else mtype
        user_data = payload.get("user") or {}
        user_name = user_data.get("display_name") if isinstance(user_data, dict) else str(user_data or "")

        vars_payload = {
            "user_name": user_name,
            "display_name": user_name,
            "platform": platform,
            "type": mtype,
            "amount": str(payload.get("display_amount") or payload.get("amount_micros", "")),
            "display_amount": str(payload.get("display_amount", "")),
            "currency": str(payload.get("currency", "")),
            "message": str(payload.get("message", "")),
        }
        self._broadcast_by_need("needs_alerts", {
            "type": "alert",
            "data": {
                "type": event_type,
                "data": vars_payload,
                "raw": payload,
            }
        })
        if mtype in ("superchat", "bits", "cheer", "supersticker"):
            await self._set_vars({
                "donations.latest_name": user_name,
                "donations.latest_amount": str(payload.get("display_amount") or payload.get("amount_micros", "")),
                "donations.latest_platform": platform,
            })

    async def _on_youtube_superchat(self, event):
        payload = event.payload or {}
        user = payload.get("user", "")
        amount = payload.get("display_amount", "")
        msg = payload.get("message", "")
        vars_payload = {
            "user_name": user,
            "display_name": user,
            "platform": "youtube",
            "type": "superchat",
            "amount": str(amount),
            "display_amount": str(amount),
            "currency": str(payload.get("currency", "")),
            "message": str(msg),
        }
        self._broadcast_by_need("needs_alerts", {
            "type": "alert",
            "data": {
                "type": "youtube.superchat",
                "data": vars_payload,
                "raw": payload,
            }
        })
        await self._set_vars({
            "donations.latest_name": user,
            "donations.latest_amount": str(amount),
            "donations.latest_platform": "youtube",
        })

    async def _on_youtube_supersticker(self, event):
        payload = event.payload or {}
        user = payload.get("user", "")
        amount = payload.get("display_amount", "")
        vars_payload = {
            "user_name": user,
            "display_name": user,
            "platform": "youtube",
            "type": "supersticker",
            "amount": str(amount),
            "display_amount": str(amount),
            "message": str(payload.get("message", "")),
        }
        self._broadcast_by_need("needs_alerts", {
            "type": "alert",
            "data": {
                "type": "youtube.supersticker",
                "data": vars_payload,
                "raw": payload,
            }
        })

    async def _on_custom_alert_trigger(self, event):
        payload = event.payload or {}
        event_type = payload.get("type", "custom.event")
        data = payload.get("data") or payload.get("vars") or {}
        self._broadcast_by_need("needs_alerts", {
            "type": "alert",
            "data": {"type": event_type, "data": data}
        })

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

        # Built-in "latest event" vars (readable as stats["followers.latest_name"], etc.)
        latest = self._latest_event_vars(event_type, payload)
        if latest:
            await self._set_vars(latest)

        # All events go to alert overlays, except the noisy/duplicate ones
        # (see _EXCLUDED_TWITCH_EVENTS at module level).
        if event_type not in _EXCLUDED_TWITCH_EVENTS:
            self._broadcast_by_need("needs_alerts", {
                "type": "alert", "data": {"type": event_type, "data": payload}
            })

    async def _on_config_updated(self, event):
        data = event.payload
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

        entry.update(self._resolve_needs(config))
        self._broadcast_to_overlay(overlay_id, {
            "type": "config_updated",
            "data": {"overlay_id": overlay_id, "config": config, "stats": await self._current_stats()}
        })

    def _latest_event_vars(self, event_type: str, payload: dict) -> dict:
        name = payload.get("user_name") or payload.get("user_login") or ""
        if event_type == "channel.follow" and name:
            return {"followers.latest_name": name}
        if event_type in ("channel.subscribe", "channel.subscription.message") and name:
            return {"subscribers.latest_name": name,
                    "subscribers.latest_tier": payload.get("tier", "")}
        if event_type == "channel.cheer":
            return {"cheers.latest_name": name or "Anónimo",
                    "cheers.latest_bits": payload.get("bits", "")}
        if event_type == "channel.raid":
            raider = name or payload.get("from_broadcaster_user_name", "")
            if raider:
                return {"raids.latest_name": raider,
                        "raids.latest_viewers": payload.get("viewers", "")}
        return {}

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

        needs = self._resolve_needs(config)

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
            "stream.online":   await self.state.get("online", default=False, namespace="stream_state"),
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

        # Dynamic vars pool on top (may override base keys intentionally)
        stats.update(self._vars)
        return stats
