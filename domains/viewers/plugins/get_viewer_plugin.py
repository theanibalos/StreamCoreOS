from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class ViewerData(BaseModel):
    id: int
    twitch_id: str
    login: str
    display_name: str
    points: int
    total_earned: int
    is_regular: bool
    first_seen: str
    last_seen: str


class GetViewerResponse(BaseModel):
    success: bool
    data: Optional[ViewerData] = None
    error: Optional[str] = None


class GetViewerPlugin(BasePlugin):
    """GET /viewers/{login} — Fetch a viewer's profile by login name."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/viewers/{login}", "GET", self.execute,
            tags=["Viewers"],
            response_model=GetViewerResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            row = await self.db.query_one(
                "SELECT * FROM viewers WHERE lower(login) LIKE lower($1) ORDER BY points DESC LIMIT 1",
                [f"%{data['login']}%"]
            )
            if not row:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Viewer not found"}
            return {"success": True, "data": {**row, "is_regular": bool(row["is_regular"])}}
        except Exception as e:
            self.logger.error(f"[GetViewer] {e}")
            return {"success": False, "error": str(e)}
