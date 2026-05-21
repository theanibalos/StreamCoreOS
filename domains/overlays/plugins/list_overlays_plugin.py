from typing import List, Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class OverlayItem(BaseModel):
    id: int
    name: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ListOverlaysResponse(BaseModel):
    success: bool
    data: List[OverlayItem] = []
    error: Optional[str] = None


class ListOverlaysPlugin(BasePlugin):
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays", "GET", self.execute,
            tags=["Overlays"],
            response_model=ListOverlaysResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rows = await self.db.query(
                "SELECT id, name, created_at, updated_at FROM overlays ORDER BY created_at DESC"
            )
            return {"success": True, "data": rows}
        except Exception as e:
            self.logger.error(f"[ListOverlays] {e}")
            return {"success": False, "error": str(e)}
