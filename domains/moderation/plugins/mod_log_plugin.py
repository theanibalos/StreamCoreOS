from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class ModLogEntry(BaseModel):
    id: int
    platform: str = "twitch"
    channel_id: Optional[str] = None
    user_id: str = ""
    twitch_id: Optional[str] = None
    display_name: str
    action: str
    reason: str
    rule_id: Optional[int] = None
    created_at: str


class ModLogResponse(BaseModel):
    success: bool
    data: Optional[list[ModLogEntry]] = None
    error: Optional[str] = None


class ModLogPlugin(BasePlugin):
    """GET /moderation/log — Moderation action history. Query params: limit, offset, platform, channel_id, user_id/twitch_id."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/moderation/log", "GET", self.execute,
            tags=["Moderation"],
            response_model=ModLogResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            limit = max(1, min(int(data.get("limit", 50)), 200))
            offset = int(data.get("offset", 0))
            platform = data.get("platform")
            channel_id = data.get("channel_id")
            user_id = data.get("user_id") or data.get("twitch_id")

            clauses = []
            params = []
            if platform:
                params.append(platform)
                clauses.append(f"platform=${len(params)}")
            if channel_id:
                params.append(channel_id)
                clauses.append(f"channel_id=${len(params)}")
            if user_id:
                params.append(user_id)
                clauses.append(f"user_id=${len(params)}")

            where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            params.extend([limit, offset])
            rows = await self.db.query(
                f"SELECT * FROM mod_log{where} ORDER BY created_at DESC LIMIT ${len(params)-1} OFFSET ${len(params)}",
                params,
            )
            return {"success": True, "data": rows}
        except Exception as e:
            self.logger.error(f"[ModLog] {e}")
            return {"success": False, "error": str(e)}
