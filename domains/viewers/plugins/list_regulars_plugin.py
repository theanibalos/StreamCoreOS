from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class RegularEntry(BaseModel):
    twitch_id: str
    login: str
    display_name: str
    points: int
    first_seen: str


class ListRegularsResponse(BaseModel):
    success: bool
    data: Optional[list[RegularEntry]] = None
    error: Optional[str] = None


class ListRegularsPlugin(BasePlugin):
    """GET /viewers/regulars — List all regulars."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/viewers/regulars", "GET", self.execute,
            tags=["Viewers"],
            response_model=ListRegularsResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rows = await self.db.query(
                "SELECT twitch_id, login, display_name, points, first_seen FROM viewers WHERE is_regular=1 ORDER BY display_name"
            )
            return {"success": True, "data": [dict(r) for r in rows]}
        except Exception as e:
            self.logger.error(f"[ListRegulars] {e}")
            return {"success": False, "error": str(e)}
