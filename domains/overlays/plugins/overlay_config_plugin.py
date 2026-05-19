import json
from typing import Optional, Any
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class OverlayConfigResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


class OverlayConfigPlugin(BasePlugin):
    """
    Public endpoint (no auth) — used by the OBS browser source renderer.
    Returns the overlay config JSON for a given overlay id.
    """

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/overlays/{id}/config", "GET", self.execute,
            tags=["Overlays"],
            response_model=OverlayConfigResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            overlay_id = data.get("id")
            row = await self.db.query_one(
                "SELECT id, name, config FROM overlays WHERE id = $1", [overlay_id]
            )
            if not row:
                return {"success": False, "error": "Overlay not found"}

            row["config"] = json.loads(row["config"])
            return {"success": True, "data": row}
        except Exception as e:
            self.logger.error(f"[OverlayConfig] {e}")
            return {"success": False, "error": str(e)}
