from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin


class AdjustPointsRequest(BaseModel):
    delta: int = Field(description="Points to add (positive) or remove (negative).")


class ViewerData(BaseModel):
    global_user_id: str
    platform: str
    platform_user_id: str
    display_name: str
    points: int
    total_earned: int


class AdjustPointsResponse(BaseModel):
    success: bool
    data: Optional[ViewerData] = None
    error: Optional[str] = None


class AdjustPointsPlugin(BasePlugin):
    """POST /viewers/{global_user_id}/points — Manually adjust a viewer's points."""

    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/viewers/{global_user_id}/points", "POST", self.execute,
            tags=["Viewers"],
            request_model=AdjustPointsRequest,
            response_model=AdjustPointsResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            global_user_id = data["global_user_id"]
            req = AdjustPointsRequest(**{k: v for k, v in data.items() if k != "global_user_id"})

            viewer = await self.db.query_one(
                "SELECT * FROM viewers WHERE global_user_id=$1", [global_user_id]
            )
            if not viewer:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Viewer not found"}

            if req.delta >= 0:
                await self.db.execute(
                    "UPDATE viewers SET points=points+$1, total_earned=total_earned+$1 WHERE global_user_id=$2",
                    [req.delta, global_user_id],
                )
            else:
                await self.db.execute(
                    "UPDATE viewers SET points=MAX(0, points+$1) WHERE global_user_id=$2",
                    [req.delta, global_user_id],
                )

            updated = await self.db.query_one(
                """SELECT global_user_id, platform, platform_user_id, display_name, points, total_earned
                   FROM viewers WHERE global_user_id=$1""",
                [global_user_id],
            )

            if req.delta != 0:
                await self.bus.publish("viewer.points.awarded", {
                    "global_user_id": global_user_id,
                    "platform": updated["platform"],
                    "platform_user_id": updated["platform_user_id"],
                    "display_name": updated["display_name"],
                    "delta": req.delta,
                })

            return {"success": True, "data": dict(updated)}
        except Exception as e:
            self.logger.error(f"[AdjustPoints] {e}")
            return {"success": False, "error": str(e)}
