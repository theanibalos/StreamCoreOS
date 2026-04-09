from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class OAuthStartResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class TwitchOAuthStartPlugin(BasePlugin):
    """
    GET /auth/twitch

    Returns the Twitch OAuth authorization URL with all scopes accumulated
    from registered plugins. The caller (browser or frontend) navigates to
    this URL so the streamer can grant permissions.
    """

    def __init__(self, twitch, http, logger):
        self.twitch = twitch
        self.http = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/auth/twitch",
            "GET",
            self.execute,
            tags=["Twitch Auth"],
            response_model=OAuthStartResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            url, _ = self.twitch.get_auth_url()
            # State is stored internally in the twitch tool for CSRF validation
            return {"success": True, "data": {"auth_url": url}}
        except Exception as e:
            self.logger.error(f"[TwitchOAuthStart] {e}")
            return {"success": False, "error": str(e)}
