from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class LeaderboardEntry(BaseModel):
    rank: int
    global_user_id: str
    platform: str
    platform_user_id: str
    display_name: str
    points: int
    total_earned: int
    is_regular: bool


class LeaderboardResponse(BaseModel):
    success: bool
    data: Optional[list[LeaderboardEntry]] = None
    error: Optional[str] = None


class LeaderboardPlugin(BasePlugin):
    """GET /viewers/leaderboard?limit=10 — Top viewers by points across platforms."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/viewers/leaderboard", "GET", self.execute,
            tags=["Viewers"],
            response_model=LeaderboardResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            limit = min(int(data.get("limit", 10)), 100)
            rows = await self.db.query(
                """SELECT global_user_id, platform, platform_user_id, display_name,
                          points, total_earned, is_regular
                   FROM viewers ORDER BY points DESC LIMIT $1""",
                [limit],
            )
            entries = [
                {**r, "rank": i + 1, "is_regular": bool(r["is_regular"])}
                for i, r in enumerate(rows)
            ]
            return {"success": True, "data": entries}
        except Exception as e:
            self.logger.error(f"[Leaderboard] {e}")
            return {"success": False, "error": str(e)}
