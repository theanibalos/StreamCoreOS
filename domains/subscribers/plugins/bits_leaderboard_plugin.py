from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class BitsEntry(BaseModel):
    rank: int
    twitch_id: str
    display_name: str
    bits_total: int
    last_cheer_at: str


class BitsLeaderboardResponse(BaseModel):
    success: bool
    data: Optional[list[BitsEntry]] = None
    error: Optional[str] = None


class BitsLeaderboardPlugin(BasePlugin):
    """GET /bits/leaderboard?limit=10"""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/bits/leaderboard", "GET", self.execute,
            tags=["Subscribers"],
            response_model=BitsLeaderboardResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            limit = min(int(data.get("limit", 10)), 100)
            rows = await self.db.query(
                """SELECT twitch_id, display_name, bits_total, last_cheer_at
                   FROM viewer_bits
                   ORDER BY bits_total DESC
                   LIMIT $1""",
                [limit],
            )
            entries = [{**r, "rank": i + 1} for i, r in enumerate(rows)]
            return {"success": True, "data": entries}
        except Exception as e:
            self.logger.error(f"[BitsLeaderboard] {e}")
            return {"success": False, "error": str(e)}
