from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class SubscriberEntry(BaseModel):
    rank: int
    twitch_id: str
    display_name: str
    tier: str
    is_prime: bool
    is_gift: bool
    cumulative_months: int
    streak_months: Optional[int]
    subscribed_at: str
    is_active: bool


class SubscribersLeaderboardResponse(BaseModel):
    success: bool
    data: Optional[list[SubscriberEntry]] = None
    total: Optional[int] = None
    error: Optional[str] = None


_SORT_COLUMNS = {
    "months": "cumulative_months DESC, tier DESC",
    "tier":   "tier DESC, cumulative_months DESC",
    "streak": "COALESCE(streak_months, 0) DESC, cumulative_months DESC",
}


class SubscribersLeaderboardPlugin(BasePlugin):
    """
    GET /subscribers/leaderboard
      ?sort=months|tier|streak   (default: months)
      &limit=10                  (max 100)
      &active_only=true          (default: true)
    """

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/subscribers/leaderboard", "GET", self.execute,
            tags=["Subscribers"],
            response_model=SubscribersLeaderboardResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            sort       = data.get("sort", "months")
            limit      = min(int(data.get("limit", 20)), 100)
            offset     = max(int(data.get("offset", 0)), 0)
            active_only  = str(data.get("active_only", "true")).lower() != "false"
            exclude_gift = str(data.get("exclude_gift", "false")).lower() == "true"

            order = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["months"])
            conditions = []
            if active_only:
                conditions.append("is_active=1")
            if exclude_gift:
                conditions.append("is_gift=0")
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            count_rows = await self.db.query(
                f"SELECT COUNT(*) AS n FROM subscribers {where}", []
            )
            total = count_rows[0]["n"] if count_rows else 0

            rows = await self.db.query(
                f"""SELECT twitch_id, display_name, tier, is_prime, is_gift,
                           cumulative_months, streak_months, subscribed_at, is_active
                    FROM subscribers {where}
                    ORDER BY {order}
                    LIMIT $1 OFFSET $2""",
                [limit, offset],
            )
            entries = [
                {
                    **r,
                    "rank": offset + i + 1,
                    "is_prime": bool(r["is_prime"]),
                    "is_gift": bool(r["is_gift"]),
                    "is_active": bool(r["is_active"]),
                }
                for i, r in enumerate(rows)
            ]
            return {"success": True, "data": entries, "total": total}
        except Exception as e:
            self.logger.error(f"[SubscribersLeaderboard] {e}")
            return {"success": False, "error": str(e)}
