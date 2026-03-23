from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class TransactionData(BaseModel):
    id: int
    amount: int
    reason: str
    created_at: str


class PointsHistoryResponse(BaseModel):
    success: bool
    data: Optional[list[TransactionData]] = None
    error: Optional[str] = None


class PointsHistoryPlugin(BasePlugin):
    """GET /loyalty/viewers/{twitch_id}/history — Transaction history for a viewer."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/loyalty/viewers/{twitch_id}/history", "GET", self.execute,
            tags=["Loyalty"],
            response_model=PointsHistoryResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            limit = max(1, min(int(data.get("limit", 50)), 200))
            offset = int(data.get("offset", 0))
            identifier = data["twitch_id"]

            if identifier.isdigit():
                twitch_id = identifier
            else:
                viewer = await self.db.query_one(
                    "SELECT twitch_id FROM viewer_points WHERE lower(display_name)=lower($1)",
                    [identifier],
                )
                if not viewer:
                    if context:
                        context.set_status(404)
                    return {"success": False, "error": "Viewer not found"}
                twitch_id = viewer["twitch_id"]

            rows = await self.db.query(
                """SELECT id, amount, reason, created_at FROM points_transactions
                   WHERE twitch_id=$1 ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
                [twitch_id, limit, offset],
            )
            return {"success": True, "data": rows}
        except Exception as e:
            self.logger.error(f"[PointsHistory] {e}")
            return {"success": False, "error": str(e)}
