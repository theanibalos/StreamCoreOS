import json
from typing import Optional, Any
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class OverlayData(BaseModel):
    id: int
    name: str
    config: Any
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class GetOverlayResponse(BaseModel):
    success: bool
    data: Optional[OverlayData] = None
    error: Optional[str] = None


class GetOverlayPlugin(BasePlugin):
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays/{id}", "GET", self.execute,
            tags=["Overlays"],
            response_model=GetOverlayResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            overlay_id = data.get("id")
            try:
                oid = int(overlay_id) if overlay_id is not None else None
            except (ValueError, TypeError):
                oid = overlay_id

            row = await self.db.query_one(
                "SELECT * FROM overlays WHERE id = $1", [oid]
            )
            if not row:
                return {"success": False, "error": "Overlay not found"}

            row["config"] = json.loads(row["config"])
            return {"success": True, "data": row}
        except Exception as e:
            self.logger.error(f"[GetOverlay] {e}")
            return {"success": False, "error": str(e)}
