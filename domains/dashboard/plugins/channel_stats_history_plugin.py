from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class StatsSnapshot(BaseModel):
    id: int
    recorded_at: str
    viewer_count: int
    follower_count: int


class StatsHistoryResponse(BaseModel):
    success: bool
    data: Optional[list[StatsSnapshot]] = None
    error: Optional[str] = None


class ChannelStatsHistoryPlugin(BasePlugin):
    """
    GET /dashboard/stats/history

    Returns historical channel stats snapshots collected every 5 minutes.
    Query params: limit (default 50, max 500), offset (default 0).
    """

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/dashboard/stats/history",
            "GET",
            self.execute,
            tags=["Dashboard"],
            response_model=StatsHistoryResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            limit = max(1, min(int(data.get("limit", 50)), 500))
            offset = int(data.get("offset", 0))
            rows = await self.db.query(
                """SELECT id, recorded_at, viewer_count, follower_count
                   FROM channel_stats
                   ORDER BY recorded_at DESC
                   LIMIT $1 OFFSET $2""",
                [limit, offset],
            )
            return {"success": True, "data": rows}
        except Exception as e:
            self.logger.error(f"[StatsHistory] {e}")
            return {"success": False, "error": str(e)}
