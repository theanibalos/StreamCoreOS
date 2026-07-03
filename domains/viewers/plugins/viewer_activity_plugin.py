from core.base_plugin import BasePlugin

_POINTS_PER_MESSAGE = 1


class ViewerActivityPlugin(BasePlugin):
    """
    Awards points to viewers on every chat message.

    Upserts the viewer record on first appearance, then increments
    points and total_earned on each subsequent message.

    Publishes: viewer.points.awarded
    """

    def __init__(self, event_bus, db, logger):
        self.bus = event_bus
        self.db = db
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.message.received", self._on_message)

    async def _on_message(self, event):
        data = event.payload
        twitch_id = data.get("user_id", "")
        login = data.get("nick", "")
        display_name = data.get("display_name", login)

        if not twitch_id:
            return

        try:
            await self.db.execute(
                """INSERT INTO viewers (twitch_id, login, display_name, points, total_earned)
                   VALUES ($1, $2, $3, $4, $4)
                   ON CONFLICT(twitch_id) DO UPDATE SET
                       login         = excluded.login,
                       display_name  = excluded.display_name,
                       points        = points + $4,
                       total_earned  = total_earned + $4,
                       last_seen     = datetime('now')""",
                [twitch_id, login, display_name, _POINTS_PER_MESSAGE],
            )
            await self.bus.publish("viewer.points.awarded", {
                "twitch_id": twitch_id,
                "display_name": display_name,
                "delta": _POINTS_PER_MESSAGE,
            })
        except Exception as e:
            self.logger.error(f"[ViewerActivity] {e}")
