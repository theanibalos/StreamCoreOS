from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class ViewerData(BaseModel):
    id: int
    global_user_id: str
    platform: str
    platform_user_id: str
    login: Optional[str] = None
    display_name: str
    avatar_url: Optional[str] = None
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
    """GET /viewers/{query} — Fetch a viewer by global id, platform id, or login."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/viewers/{query}", "GET", self.execute,
            tags=["Viewers"],
            response_model=GetViewerResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            query = data["query"]
            row = await self.db.query_one(
                """SELECT * FROM viewers
                   WHERE lower(global_user_id)=lower($1)
                      OR lower(platform_user_id)=lower($1)
                      OR lower(login) LIKE lower($2)
                   ORDER BY points DESC LIMIT 1""",
                [query, f"%{query}%"],
            )
            if not row:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Viewer not found"}
            return {"success": True, "data": {**row, "is_regular": bool(row["is_regular"])}}
        except Exception as e:
            self.logger.error(f"[GetViewer] {e}")
            return {"success": False, "error": str(e)}
