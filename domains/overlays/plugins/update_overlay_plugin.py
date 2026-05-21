import json
from typing import Optional, Any
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class UpdateOverlayRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    config: Optional[Any] = None


class OverlayData(BaseModel):
    id: int
    name: str
    config: Any
    updated_at: Optional[str] = None


class UpdateOverlayResponse(BaseModel):
    success: bool
    data: Optional[OverlayData] = None
    error: Optional[str] = None


class UpdateOverlayPlugin(BasePlugin):
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays/{id}", "PUT", self.execute,
            tags=["Overlays"],
            request_model=UpdateOverlayRequest,
            response_model=UpdateOverlayResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            overlay_id = data.get("id")
            req = UpdateOverlayRequest(**data)

            updates = ["updated_at = CURRENT_TIMESTAMP"]
            params = []

            if req.name is not None:
                updates.append(f"name = ${len(params)+1}")
                params.append(req.name)
            if req.config is not None:
                updates.append(f"config = ${len(params)+1}")
                params.append(json.dumps(req.config))

            params.append(overlay_id)
            await self.db.execute(
                f"UPDATE overlays SET {', '.join(updates)} WHERE id = ${len(params)}",
                params,
            )

            row = await self.db.query_one(
                "SELECT id, name, config, updated_at FROM overlays WHERE id = $1",
                [overlay_id],
            )
            if not row:
                return {"success": False, "error": "Overlay not found"}

            row["config"] = json.loads(row["config"])
            return {"success": True, "data": row}
        except Exception as e:
            self.logger.error(f"[UpdateOverlay] {e}")
            return {"success": False, "error": str(e)}
