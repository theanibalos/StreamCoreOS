from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin


class UnbanRequest(BaseModel):
    platform: str = Field(default="twitch", min_length=1)
    channel_id: Optional[str] = Field(default=None)
    user_id: Optional[str] = Field(default=None)
    twitch_id: Optional[str] = Field(default=None)
    display_name: Optional[str] = Field(default=None)


class UnbanResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class ManualUnbanPlugin(BasePlugin):
    """POST /moderation/unban — Request a platform-scoped unban."""

    def __init__(self, http, twitch, event_bus, logger):
        self.http = http
        self.twitch = twitch
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/moderation/unban", "POST", self.execute,
            tags=["Moderation"],
            request_model=UnbanRequest,
            response_model=UnbanResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = UnbanRequest(**data)
            platform = req.platform or "twitch"
            identifier = req.user_id or req.twitch_id
            if not identifier:
                return {"success": False, "error": "user_id is required"}
            if platform == "youtube":
                return {"success": False, "error": "YouTube unban requires a liveChatBan id and is not supported from user id"}

            user_id = identifier
            display_name = req.display_name or identifier
            if platform == "twitch":
                session = self.twitch.get_session()
                if not session:
                    return {"success": False, "error": "Twitch session not active"}
                user_id, display_name = await self._resolve(identifier, session["access_token"])
                if not user_id:
                    return {"success": False, "error": f"User '{identifier}' not found on Twitch"}

            await self.bus.publish("moderation.action.requested", {
                "platform": platform,
                "channel_id": req.channel_id,
                "user": {"id": f"{platform}:{user_id}", "platform_id": user_id, "display_name": display_name},
                "action": "unban",
                "duration_s": None,
                "reason": "Manual unban",
            })
            return {"success": True, "data": {"platform": platform, "user_id": user_id, "display_name": display_name}}
        except Exception as e:
            self.logger.error(f"[ManualUnban] {e}")
            return {"success": False, "error": str(e)}

    async def _resolve(self, identifier: str, access_token: str) -> tuple[str | None, str]:
        if identifier.isdigit():
            return identifier, identifier
        result = await self.twitch.get("/users", params={"login": identifier}, user_token=access_token)
        users = result.get("data", [])
        if not users:
            return None, identifier
        return users[0]["id"], users[0]["display_name"]
