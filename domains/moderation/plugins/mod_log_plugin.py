from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class ModLogEntry(BaseModel):
    id: int
    twitch_id: str
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
    """GET /moderation/log — Moderation action history. Query params: limit, offset, twitch_id."""

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
            twitch_id = data.get("twitch_id")

            if twitch_id:
                rows = await self.db.query(
                    """SELECT * FROM mod_log WHERE twitch_id=$1
                       ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
                    [twitch_id, limit, offset],
                )
            else:
                rows = await self.db.query(
                    "SELECT * FROM mod_log ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                    [limit, offset],
                )
            return {"success": True, "data": rows}
        except Exception as e:
            self.logger.error(f"[ModLog] {e}")
            return {"success": False, "error": str(e)}
