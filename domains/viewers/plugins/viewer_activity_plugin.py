from microcoreos.base_plugin import BasePlugin

_POINTS_PER_MESSAGE = 1


class ViewerActivityPlugin(BasePlugin):
    """
    Awards points to viewers on every chat message.

    Upserts by global_user_id (for example twitch:123 or youtube:UC...) and keeps
    platform/platform_user_id as first-class viewer identity fields.
    """

    def __init__(self, event_bus, db, logger):
        self.bus = event_bus
        self.db = db
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.message.received", self._on_message)

    async def _on_message(self, event):
        data = event.payload
        platform = data.get("platform") or "twitch"
        user = data.get("user") or {}
        global_user_id = user.get("id") or ""
        platform_user_id = user.get("platform_id") or ""
        login = user.get("login") or None
        display_name = user.get("display_name") or login or platform_user_id
        avatar_url = user.get("avatar_url")

        if not global_user_id or not platform_user_id or not display_name:
            return

        try:
            await self.db.execute(
                """INSERT INTO viewers (
                       global_user_id, platform, platform_user_id, login, display_name,
                       avatar_url, points, total_earned
                   )
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
                   ON CONFLICT(global_user_id) DO UPDATE SET
                       platform         = excluded.platform,
                       platform_user_id = excluded.platform_user_id,
                       login            = excluded.login,
                       display_name     = excluded.display_name,
                       avatar_url       = excluded.avatar_url,
                       points           = points + $7,
                       total_earned     = total_earned + $7,
                       last_seen        = datetime('now')""",
                [
                    global_user_id,
                    platform,
                    platform_user_id,
                    login,
                    display_name,
                    avatar_url,
                    _POINTS_PER_MESSAGE,
                ],
            )
            await self.bus.publish("viewer.points.awarded", {
                "global_user_id": global_user_id,
                "platform": platform,
                "platform_user_id": platform_user_id,
                "display_name": display_name,
                "delta": _POINTS_PER_MESSAGE,
            })
        except Exception as e:
            self.logger.error(f"[ViewerActivity] {e}")
