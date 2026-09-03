from datetime import datetime, timezone
from core.base_plugin import BasePlugin


class BitsTrackerPlugin(BasePlugin):
    """
    Accumulates bits per viewer in viewer_bits table.

    channel.cheer      → cheers clásicos
    channel.bits.use   → cheers + Power-ups (evento nuevo de Twitch)

    Publishes: viewer.bits.received
    """

    def __init__(self, twitch, db, event_bus, logger):
        self.twitch = twitch
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.twitch.register(
            "channel.cheer", "1",
            scopes=["bits:read"],
        )
        self.twitch.register(
            "channel.bits.use", "1",
            scopes=["bits:read"],
        )

        self.twitch.on_event("channel.cheer", self._on_cheer)
        self.twitch.on_event("channel.bits.use", self._on_bits_use)

    async def _on_cheer(self, event: dict):
        if event.get("is_anonymous", False):
            return
        await self._record(
            event.get("user_id", ""),
            event.get("user_login", ""),
            event.get("user_name", ""),
            event.get("bits", 0),
            event.get("broadcaster_user_id"),
        )

    async def _on_bits_use(self, event: dict):
        user_id = event.get("user_id", "")
        if not user_id:
            return
        await self._record(
            user_id,
            event.get("user_login", ""),
            event.get("user_name", ""),
            event.get("bits", 0),
            event.get("broadcaster_user_id"),
        )

    async def _record(self, twitch_id: str, login: str, display_name: str, bits: int, channel_id: str | None = None):
        if not twitch_id or bits <= 0:
            return
        try:
            await self.db.execute(
                """INSERT INTO viewer_bits (twitch_id, login, display_name, bits_total, last_cheer_at)
                   VALUES ($1, $2, $3, $4, datetime('now'))
                   ON CONFLICT(twitch_id) DO UPDATE SET
                       login         = excluded.login,
                       display_name  = excluded.display_name,
                       bits_total    = bits_total + $4,
                       last_cheer_at = datetime('now')""",
                [twitch_id, login, display_name, bits],
            )
            await self.bus.publish("viewer.bits.received", {
                "twitch_id": twitch_id,
                "display_name": display_name,
                "bits": bits,
            })
            await self.bus.publish("monetization.event.received", {
                "platform": "twitch",
                "channel_id": channel_id,
                "type": "bits",
                "user": {
                    "id": f"twitch:{twitch_id}",
                    "platform_id": twitch_id,
                    "login": login,
                    "display_name": display_name,
                },
                "amount_micros": None,
                "currency": None,
                "display_amount": f"{bits} bits",
                "message": "",
                "raw": {"bits": bits},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            self.logger.error(f"[BitsTracker] {e}")
