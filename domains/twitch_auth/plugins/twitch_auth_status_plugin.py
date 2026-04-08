from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class AuthStatusData(BaseModel):
    connected: bool
    login: Optional[str] = None
    broadcaster_id: Optional[str] = None


class AuthStatusResponse(BaseModel):
    success: bool
    data: Optional[AuthStatusData] = None
    error: Optional[str] = None


class TwitchAuthStatusPlugin(BasePlugin):
    """GET /auth/twitch/status — Returns whether a Twitch session is active."""

    def __init__(self, twitch, http, logger):
        self.twitch = twitch
        self.http = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/auth/twitch/status",
            "GET",
            self.execute,
            tags=["Twitch Auth"],
            response_model=AuthStatusResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            session = self.twitch.get_session()
            if session:
                return {
                    "success": True,
                    "data": {
                        "connected": True,
                        "login": session["login"],
                        "broadcaster_id": session["broadcaster_id"],
                    },
                }
            return {"success": True, "data": {"connected": False}}
        except Exception as e:
            self.logger.error(f"[TwitchAuthStatus] {e}")
            return {"success": False, "error": str(e)}
