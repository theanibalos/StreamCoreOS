from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class UnbanRequest(BaseModel):
    twitch_id: str = Field(min_length=1)


class UnbanResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class ManualUnbanPlugin(BasePlugin):
    """POST /moderation/unban — Unban a user via Helix API."""

    def __init__(self, http, twitch, db, logger):
        self.http = http
        self.twitch = twitch
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/moderation/unban", "POST", self.execute,
            tags=["Moderation"],
            request_model=UnbanRequest,
            response_model=UnbanResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = UnbanRequest(**data)
            session = self.twitch.get_session()
            if not session:
                return {"success": False, "error": "Twitch session not active"}
            broadcaster_id = session["broadcaster_id"]
            access_token = session["access_token"]
            if not broadcaster_id or not access_token:
                return {"success": False, "error": "Twitch session not active"}

            twitch_id, display_name = await self._resolve(req.twitch_id, access_token)
            if not twitch_id:
                return {"success": False, "error": f"User '{req.twitch_id}' not found on Twitch"}

            await self.twitch.delete(
                "/moderation/bans",
                params={"broadcaster_id": broadcaster_id, "moderator_id": broadcaster_id, "user_id": twitch_id},
                user_token=access_token,
            )
            await self.db.execute(
                "INSERT INTO mod_log (twitch_id, display_name, action, reason) VALUES ($1,$2,$3,$4)",
                [twitch_id, display_name, "unban", "Manual unban"],
            )
            return {"success": True, "data": {"twitch_id": twitch_id, "display_name": display_name}}
        except Exception as e:
            self.logger.error(f"[ManualUnban] {e}")
            return {"success": False, "error": str(e)}

    async def _resolve(self, identifier: str, access_token: str) -> tuple[str | None, str]:
        """Returns (twitch_id, display_name). Resolves username via Helix if needed."""
        if identifier.isdigit():
            return identifier, identifier
        result = await self.twitch.get("/users", params={"login": identifier}, user_token=access_token)
        users = result.get("data", [])
        if not users:
            return None, identifier
        return users[0]["id"], users[0]["display_name"]
