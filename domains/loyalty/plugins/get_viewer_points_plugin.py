from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class ViewerPointsData(BaseModel):
    twitch_id: str
    display_name: str
    points: int
    total_earned: int


class ViewerPointsResponse(BaseModel):
    success: bool
    data: Optional[ViewerPointsData] = None
    error: Optional[str] = None


class GetViewerPointsPlugin(BasePlugin):
    """GET /loyalty/viewers/{twitch_id} — Get a viewer's current point balance."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/loyalty/viewers/{twitch_id}", "GET", self.execute,
            tags=["Loyalty"],
            response_model=ViewerPointsResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            identifier = data["twitch_id"]
            if identifier.isdigit():
                viewer = await self.db.query_one(
                    "SELECT twitch_id, display_name, points, total_earned FROM viewer_points WHERE twitch_id=$1",
                    [identifier],
                )
            else:
                viewer = await self.db.query_one(
                    "SELECT twitch_id, display_name, points, total_earned FROM viewer_points WHERE lower(display_name)=lower($1)",
                    [identifier],
                )
            if not viewer:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Viewer not found"}
            return {"success": True, "data": viewer}
        except Exception as e:
            self.logger.error(f"[GetViewerPoints] {e}")
            return {"success": False, "error": str(e)}
