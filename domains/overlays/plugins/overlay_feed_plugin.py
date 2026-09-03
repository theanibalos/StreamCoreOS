import asyncio
import json
import secrets
import time
from collections import deque
from typing import Optional
from microcoreos.base_plugin import BasePlugin

# ── Contract constants (see StreamCoreOS-Front/OVERLAY_FEED_CONTRACT.md) ──────
_CONTRACT_VERSION = 1
_EMOTE_CDN = "https://static-cdn.jtvnw.net/emoticons/v2/{id}/{fmt}/dark/2.0"

# Raw Twitch EventSub type → clean contract `event.*` name. Mapping lives here,
# in the backend, the ONLY place that should know Twitch's vocabulary. Overlays
# never see the raw names.
_EVENT_MAP = {
    "channel.follow":                 "event.follow",
    "channel.subscribe":              "event.subscription",
    "channel.subscription.message":   "event.subscription",
    "channel.subscription.gift":      "event.subscription.gift",
    "channel.raid":                   "event.raid",
    "channel.cheer":                  "event.cheer",
}


class OverlayFeedPlugin(BasePlugin):
    """
    GET /api/overlays/feed?token=<channel_overlay_token>   (SSE — public)

    The single, channel-wide "overlay feed": one clean, token-authed SSE stream
    that carries everything an overlay could need. Overlays are built ANYWHERE —
    hand-coded, AI-generated, or in a future in-app builder — and just consume
    this feed as tolerant readers. Contract: OVERLAY_FEED_CONTRACT.md.

    This is a NEW, parallel endpoint. The per-overlay builder stream
    (/api/overlays/stream/{id}) is untouched.

    Every message shares the envelope:
        {"type": "<namespace>", "v": 1, "ts": <epoch_ms>, "data": {...}}

    Message types:
        feed.snapshot   — first event on connect (current stats + recent chat)
        stat.update     — generic {key, value, previous, display}
        event.*         — pure fact triggers (follow/subscription/raid/cheer/...)
        chat.message    — with fragments + fully-resolved emote & badge URLs
        feed.error      — token rejected (then the stream closes)

    Auth: EventSource cannot set headers, so the token arrives as a query param
    and is validated INSIDE the generator (the http tool's auth_validator only
    reads a Bearer header, unusable here). The token is the opaque value the
    overlay_token plugin persists in the overlay_feed_token table; the feed only
    reads and compares it — the token plugin owns its lifecycle.
    """

    def __init__(self, http, db, event_bus, twitch, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.twitch = twitch
        self.logger = logger
        self._queues: list[asyncio.Queue] = []
        self._recent_chat: deque = deque(maxlen=20)
        self._last_stats: dict = {}
        self._badge_cache: dict = {}
        self._badge_cache_at: float = 0.0

    async def on_boot(self):
        await self.bus.subscribe("chat.message.received", self._on_chat)
        await self.bus.subscribe("dashboard.stats.updated", self._on_stats)
        await self.bus.subscribe("overlay.test.event", self._on_test)
        await self.bus.subscribe("youtube.superchat.received", self._on_youtube_superchat)
        await self.bus.subscribe("youtube.supersticker.received", self._on_youtube_supersticker)
        self.twitch.on_event("*", self._on_twitch_event)

        self.http.add_sse_endpoint(
            "/api/overlays/feed",
            self._stream,
            tags=["Overlays"],
        )

    # ── Envelope + fan-out ────────────────────────────────────────────

    def _envelope(self, type_: str, data: dict) -> str:
        return json.dumps({
            "type": type_,
            "v": _CONTRACT_VERSION,
            "ts": int(time.time() * 1000),
            "data": data,
        })

    def _broadcast(self, type_: str, data: dict):
        msg = self._envelope(type_, data)
        for q in self._queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    # ── Token (persisted; owned by the overlay_token plugin) ──────────

    async def _channel_token(self) -> Optional[str]:
        try:
            row = await self.db.query_one(
                "SELECT token FROM overlay_feed_token WHERE id = 1", []
            )
            return row["token"] if row else None
        except Exception:
            return None

    # ── Emote & badge resolution (the plumbing the contract absorbs) ──

    def _emote_url(self, emote_id: str, animated: bool) -> str:
        return _EMOTE_CDN.format(id=emote_id, fmt="animated" if animated else "static")

    async def _badge_map(self) -> dict:
        """Global Twitch badges as {set_id: {version: url}}, cached 1h."""
        now = time.time()
        if self._badge_cache and now - self._badge_cache_at < 3600:
            return self._badge_cache
        session = self.twitch.get_session()
        if not session:
            return self._badge_cache
        try:
            resp = await self.twitch.get(
                "/chat/badges/global", user_token=session["access_token"]
            )
            result: dict = {}
            for badge_set in resp.get("data", []):
                versions = {v["id"]: v.get("image_url_1x", "")
                            for v in badge_set.get("versions", [])}
                result[badge_set.get("set_id", "")] = versions
            self._badge_cache = result
            self._badge_cache_at = now
        except Exception as e:
            self.logger.error(f"[OverlayFeed] badge fetch: {e}")
        return self._badge_cache

    def _resolve_fragments(self, raw_fragments: list) -> list:
        out = []
        for f in raw_fragments or []:
            if f.get("type") == "emote" and f.get("emote_id"):
                out.append({
                    "type": "emote",
                    "name": f.get("text", ""),
                    "emote_id": f["emote_id"],
                    "emote_animated": bool(f.get("emote_animated")),
                    "url": self._emote_url(f["emote_id"], bool(f.get("emote_animated"))),
                })
            else:
                out.append({"type": "text", "text": f.get("text", "")})
        return out

    async def _resolve_badges(self, raw_badges) -> list:
        bmap = await self._badge_map()
        out = []
        if isinstance(raw_badges, dict):
            raw_badges = [{"set": k, "version": v} for k, v in raw_badges.items()]
        for badge in raw_badges or []:
            set_id = badge.get("set") or badge.get("set_id") or ""
            version = str(badge.get("version") or badge.get("id") or "")
            url = badge.get("url") or bmap.get(set_id, {}).get(version, "")
            out.append({"set": set_id, "version": version, "url": url})
        return out

    # ── Bus handlers ──────────────────────────────────────────────────

    async def _on_chat(self, event):
        p = event.payload or {}
        user = p.get("user") or {}
        data = {
            "id": p.get("message_id", ""),
            "platform": p.get("platform", "twitch"),
            "channel_id": p.get("channel_id", ""),
            "channel_name": p.get("channel_name", ""),
            "user": user.get("display_name", ""),
            "user_id": user.get("id", ""),
            "color": p.get("color", ""),
            "badges": await self._resolve_badges(p.get("badges", [])),
            "text": p.get("message", ""),
            "fragments": self._resolve_fragments(p.get("fragments", [])),
        }
        self._recent_chat.append(data)
        self._broadcast("chat.message", data)

    async def _on_stats(self, event):
        stats = await self._clean_stats()
        for key, value in stats.items():
            prev = self._last_stats.get(key)
            if value != prev:
                self._broadcast("stat.update", {
                    "key": key, "value": value, "previous": prev,
                    "display": self._display(value),
                })
        self._last_stats = stats

    async def _on_twitch_event(self, event_data: dict):
        raw_type = event_data.get("_event_type", "")
        contract_type = _EVENT_MAP.get(raw_type)
        if not contract_type:
            return
        p = {k: v for k, v in event_data.items() if k != "_event_type"}
        self._broadcast(contract_type, self._event_data(raw_type, p))

    async def _on_youtube_superchat(self, event):
        p = event.payload or {}
        self._broadcast("event.superchat", {
            "id": p.get("id", ""),
            "platform": "youtube",
            "user": p.get("user", ""),
            "user_id": p.get("user_id", ""),
            "amount_micros": p.get("amount_micros", 0),
            "currency": p.get("currency", ""),
            "display_amount": p.get("display_amount", ""),
            "message": p.get("message", ""),
        })

    async def _on_youtube_supersticker(self, event):
        p = event.payload or {}
        self._broadcast("event.supersticker", {
            "id": p.get("id", ""),
            "platform": "youtube",
            "user": p.get("user", ""),
            "user_id": p.get("user_id", ""),
            "display_amount": p.get("display_amount", ""),
            "message": p.get("message", ""),
        })

    async def _on_test(self, event):
        p = event.payload or {}
        type_ = p.get("type")
        data = dict(p.get("data") or {})
        if not type_:
            return
        data["test"] = True
        self._broadcast(type_, data)

    # ── Shaping ───────────────────────────────────────────────────────

    def _event_data(self, raw_type: str, p: dict) -> dict:
        user = p.get("user_name") or p.get("user_login") or p.get("from_broadcaster_user_name") or ""
        data = {
            "id": p.get("message_id") or secrets.token_hex(8),
            "user": user,
            "user_id": p.get("user_id", ""),
        }
        if raw_type in ("channel.subscribe", "channel.subscription.message", "channel.subscription.gift"):
            data["tier"] = p.get("tier", "")
            data["months"] = p.get("cumulative_months") or p.get("duration_months") or 0
            msg = p.get("message")
            data["message"] = msg.get("text", "") if isinstance(msg, dict) else (msg or "")
        elif raw_type == "channel.raid":
            data["viewers"] = p.get("viewers", 0)
        elif raw_type == "channel.cheer":
            data["bits"] = p.get("bits", 0)
            data["message"] = p.get("message", "")
        return data

    def _display(self, value) -> str:
        if isinstance(value, int) and value >= 1000:
            return f"{value / 1000:.1f}K".replace(".0K", "K")
        return str(value)

    async def _clean_stats(self) -> dict:
        stats = {"followers": 0, "subs": 0, "viewers": 0, "bits": 0}
        try:
            row = await self.db.query_one(
                "SELECT viewer_count, follower_count FROM channel_stats ORDER BY id DESC LIMIT 1", []
            )
            if row:
                stats["followers"] = int(row["follower_count"])
                stats["viewers"] = int(row["viewer_count"])
        except Exception:
            pass
        try:
            row = await self.db.query_one(
                "SELECT COUNT(*) AS n FROM subscribers WHERE is_active=1", []
            )
            stats["subs"] = int(row["n"]) if row else 0
        except Exception:
            pass
        try:
            row = await self.db.query_one(
                "SELECT COALESCE(SUM(bits_total), 0) AS n FROM viewer_bits", []
            )
            stats["bits"] = int(row["n"]) if row else 0
        except Exception:
            pass
        return stats

    # ── SSE stream ────────────────────────────────────────────────────

    async def _stream(self, data: dict):
        provided = str(data.get("token", ""))
        expected = await self._channel_token()
        if not provided or not expected or not secrets.compare_digest(provided, expected):
            yield f"data: {self._envelope('feed.error', {'error': 'invalid token'})}\n\n"
            return

        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues.append(queue)
        try:
            stats = await self._clean_stats()
            self._last_stats = stats
            snapshot = {
                "stats": stats,
                "recent_chat": list(self._recent_chat),
                "active": [],
            }
            yield f"data: {self._envelope('feed.snapshot', snapshot)}\n\n"

            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ":ping\n\n"
        finally:
            self._queues.remove(queue)
