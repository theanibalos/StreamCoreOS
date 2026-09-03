import asyncio
import json
from datetime import datetime, timezone
from microcoreos.base_plugin import BasePlugin
from typing import Optional
from pydantic import BaseModel, Field

# Internal event_bus events to forward as alerts
_BUS_EVENTS = [
    "stream.session.started",
    "stream.session.ended",
    "viewer.regular.added",
    "viewer.regular.removed",
    "moderation.action.taken",
    "dashboard.stats.updated",
]

# Raw Twitch EventSub types the wildcard listener sees but should NOT reach the
# alert feed: stream.online/offline are already covered (with richer data) by
# the internal stream.session.started/ended bus events above — forwarding
# them too would duplicate the same moment as an unstyled entry.
_EXCLUDED_TWITCH_EVENTS = {
    "stream.online",
    "stream.offline",
}


class TestAlertRequest(BaseModel):
    event_type: str = Field(default="channel.subscribe", min_length=1)
    data: Optional[dict] = Field(default=None)


class TestAlertData(BaseModel):
    event_type: str


class TestAlertResponse(BaseModel):
    success: bool
    data: Optional[TestAlertData] = None
    error: Optional[str] = None


class DashboardAlertsPlugin(BasePlugin):
    """
    GET /dashboard/alerts  (SSE)

    Streams real-time alerts to dashboard clients. Two event sources:

    1. Twitch events via twitch.on_event("*") — follows, subs, raids, cheers, etc.
    2. Internal events via event_bus.subscribe — stream on/off, reward redeems, mod actions.

    Each SSE message is a JSON object with {type, data, timestamp}.
    Per-connection queues ensure isolated, non-blocking delivery to each client.
    """

    def __init__(self, http, twitch, event_bus, logger):
        self.http = http
        self.twitch = twitch
        self.bus = event_bus
        self.logger = logger
        self._queues: list[asyncio.Queue] = []

    async def on_boot(self):
        # All Twitch EventSub events (wildcard — no new subscriptions created)
        self.twitch.on_event("*", self._on_twitch_event)

        # Internal events from other domains
        for event_name in _BUS_EVENTS:
            await self.bus.subscribe(event_name, self._make_bus_handler(event_name))

        self.http.add_sse_endpoint(
            "/api/dashboard/alerts",
            self._stream,
            tags=["Dashboard"],
        )

        # Test endpoint — pushes a fake alert without needing a real Twitch event
        self.http.add_endpoint(
            "/api/dashboard/alerts/test", "POST", self._test_alert,
            tags=["Dashboard"],
            request_model=TestAlertRequest,
            response_model=TestAlertResponse,
        )

    async def _test_alert(self, data: dict, context=None):
        event_type = data.get("event_type", "channel.subscribe")
        payload = data.get("data") or {
            "channel.follow":            {"user_name": "TestFollower", "user_login": "testfollower"},
            "channel.subscribe":         {"user_name": "TestSub", "tier": "1000", "is_gift": False},
            "channel.subscription.gift": {"user_name": "GiftKing", "total": "5", "tier": "1000"},
            "channel.cheer":             {"user_name": "BitsMaster", "bits": "1000"},
            "channel.raid":              {"from_broadcaster_user_name": "FriendStream", "viewers": "247"},
        }.get(event_type, {"user_name": "TestUser"})

        await self._push(event_type, payload)
        return {"success": True, "data": {"event_type": event_type}}

    def _make_bus_handler(self, event_name: str):
        async def handler(event):
            await self._push(event_name, event.payload)
        return handler

    async def _on_twitch_event(self, event_data: dict):
        # Wildcard handler: _event_type is injected by TwitchEventSubClient.
        # Use .get() — never .pop() — because the same dict is shared with all wildcard callbacks.
        event_type = event_data.get("_event_type", "twitch.event")
        if event_type in _EXCLUDED_TWITCH_EVENTS:
            return
        await self._push(event_type, {k: v for k, v in event_data.items() if k != "_event_type"})

    async def _push(self, event_type: str, data: dict):
        if not self._queues:
            return
        message = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        for queue in self._queues:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass  # slow client — drop

    async def _stream(self, data: dict):
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues.append(queue)
        try:
            while True:
                try:
                    alert = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {json.dumps(alert)}\n\n"
                except asyncio.TimeoutError:
                    yield 'data: {"__type":"heartbeat"}\n\n'
        finally:
            self._queues.remove(queue)
