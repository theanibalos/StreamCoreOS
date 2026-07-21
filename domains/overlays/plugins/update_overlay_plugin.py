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
    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.event_bus = event_bus
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
            try:
                oid = int(overlay_id) if overlay_id is not None else None
            except (ValueError, TypeError):
                oid = overlay_id

            req = UpdateOverlayRequest(**data)

            updates = ["updated_at = CURRENT_TIMESTAMP"]
            params = []

            if req.name is not None:
                updates.append(f"name = ${len(params)+1}")
                params.append(req.name)
            
            if req.config is not None:
                self.logger.info(f"[UpdateOverlay] Saving config for overlay {oid}: {len(json.dumps(req.config))} bytes")
                updates.append(f"config = ${len(params)+1}")
                params.append(json.dumps(req.config))

            params.append(oid)
            affected = await self.db.execute(
                f"UPDATE overlays SET {', '.join(updates)} WHERE id = ${len(params)}",
                params,
            )

            if affected == 0:
                return {"success": False, "error": f"Overlay {oid} not found"}

            row = await self.db.query_one(
                "SELECT id, name, config, updated_at FROM overlays WHERE id = $1",
                [oid],
            )
            if not row:
                return {"success": False, "error": "Overlay not found after update"}

            row["config"] = json.loads(row["config"])
            # Use the ID from the database row to ensure consistent type (int)
            await self.event_bus.publish("overlay.config.updated", {"overlay_id": row["id"]})
            return {"success": True, "data": row}
        except Exception as e:
            self.logger.error(f"[UpdateOverlay] {e}")
            return {"success": False, "error": str(e)}
