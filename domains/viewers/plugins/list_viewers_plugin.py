from typing import List, Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


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


class ListViewersResponse(BaseModel):
    success: bool
    data: Optional[List[ViewerData]] = None
    error: Optional[str] = None


class ListViewersPlugin(BasePlugin):
    """GET /viewers — List all viewers."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/viewers", "GET", self.execute,
            tags=["Viewers"],
            response_model=ListViewersResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rows = await self.db.query("SELECT * FROM viewers ORDER BY points DESC")
            viewers = [
                {**row, "is_regular": bool(row["is_regular"])}
                for row in rows
            ]
            return {"success": True, "data": viewers}
        except Exception as e:
            self.logger.error(f"[ListViewers] {e}")
            return {"success": False, "error": str(e)}
