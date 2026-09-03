from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class TwitchLogoutResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class TwitchLogoutPlugin(BasePlugin):
    """
    POST /auth/twitch/logout

    Disconnects EventSub, clears the in-memory session, and deletes the
    stored token from DB so the next boot starts unauthenticated.
    """

    def __init__(self, twitch, http, db, logger):
        self.twitch = twitch
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/auth/twitch/logout",
            "POST",
            self.execute,
            tags=["Twitch Auth"],
            response_model=TwitchLogoutResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            await self.twitch.disconnect()
            await self.db.execute("DELETE FROM twitch_tokens")
            self.logger.info("[TwitchLogout] Session cleared.")
            return {"success": True, "data": None}
        except Exception as e:
            self.logger.error(f"[TwitchLogout] {e}")
            return {"success": False, "error": str(e)}
