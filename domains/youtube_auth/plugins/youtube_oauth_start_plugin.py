from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class YouTubeOAuthStartResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class YouTubeOAuthStartPlugin(BasePlugin):
    def __init__(self, youtube, http, logger):
        self.youtube = youtube
        self.http = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/auth/youtube", "GET", self.execute,
            tags=["YouTube Auth"], response_model=YouTubeOAuthStartResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            url, _ = self.youtube.get_auth_url()
            return {"success": True, "data": {"auth_url": url}}
        except Exception as e:
            self.logger.error(f"[YouTubeOAuthStart] {e}")
            return {"success": False, "error": str(e)}
