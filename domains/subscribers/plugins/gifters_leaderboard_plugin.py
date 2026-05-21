from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class GifterEntry(BaseModel):
    rank: int
    twitch_id: str
    display_name: str
    gifts_total: int
    last_gift_at: str


class GiftersLeaderboardResponse(BaseModel):
    success: bool
    data: Optional[list[GifterEntry]] = None
    error: Optional[str] = None


class GiftersLeaderboardPlugin(BasePlugin):
    """GET /gifters/leaderboard?limit=15"""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/gifters/leaderboard", "GET", self.execute,
            tags=["Subscribers"],
            response_model=GiftersLeaderboardResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            limit = min(int(data.get("limit", 15)), 100)
            rows = await self.db.query(
                """SELECT twitch_id, display_name, gifts_total, last_gift_at
                   FROM gifters
                   ORDER BY gifts_total DESC
                   LIMIT $1""",
                [limit],
            )
            entries = [{**r, "rank": i + 1} for i, r in enumerate(rows)]
            return {"success": True, "data": entries}
        except Exception as e:
            self.logger.error(f"[GiftersLeaderboard] {e}")
            return {"success": False, "error": str(e)}
