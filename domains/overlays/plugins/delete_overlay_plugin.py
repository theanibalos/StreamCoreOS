from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class DeleteOverlayResponse(BaseModel):
    success: bool
    error: Optional[str] = None


class DeleteOverlayPlugin(BasePlugin):
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays/{id}", "DELETE", self.execute,
            tags=["Overlays"],
            response_model=DeleteOverlayResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            overlay_id = data.get("id")
            deleted = await self.db.execute(
                "DELETE FROM overlays WHERE id = $1", [overlay_id]
            )
            if not deleted:
                return {"success": False, "error": "Overlay not found"}
            return {"success": True}
        except Exception as e:
            self.logger.error(f"[DeleteOverlay] {e}")
            return {"success": False, "error": str(e)}
