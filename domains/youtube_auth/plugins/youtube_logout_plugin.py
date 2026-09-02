from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class YouTubeLogoutResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class YouTubeLogoutPlugin(BasePlugin):
    def __init__(self, youtube, http, db, logger):
        self.youtube = youtube
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/auth/youtube/logout", "POST", self.execute,
            tags=["YouTube Auth"], response_model=YouTubeLogoutResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            await self.youtube.disconnect()
            await self.db.execute("DELETE FROM youtube_tokens", [])
            return {"success": True, "data": {"logged_out": True}}
        except Exception as e:
            self.logger.error(f"[YouTubeLogout] {e}")
            return {"success": False, "error": str(e)}
