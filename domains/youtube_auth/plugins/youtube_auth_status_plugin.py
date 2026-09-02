from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class YouTubeAuthStatusData(BaseModel):
    authenticated: bool
    connected: bool
    channel_id: Optional[str] = None
    channel_title: Optional[str] = None


class YouTubeAuthStatusResponse(BaseModel):
    success: bool
    data: Optional[YouTubeAuthStatusData] = None
    error: Optional[str] = None


class YouTubeAuthStatusPlugin(BasePlugin):
    def __init__(self, youtube, http, logger):
        self.youtube = youtube
        self.http = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/auth/youtube/status", "GET", self.execute,
            tags=["YouTube Auth"], response_model=YouTubeAuthStatusResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            session = self.youtube.get_session()
            if not session:
                return {"success": True, "data": {"authenticated": False, "connected": False}}
            return {"success": True, "data": {
                "authenticated": True,
                "connected": self.youtube.is_connected(),
                "channel_id": session.get("channel_id"),
                "channel_title": session.get("channel_title"),
            }}
        except Exception as e:
            self.logger.error(f"[YouTubeAuthStatus] {e}")
            return {"success": False, "error": str(e)}
