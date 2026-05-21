import json
from typing import Optional, Any
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class CreateOverlayRequest(BaseModel):
    name: str = Field(..., min_length=1)
    config: Optional[Any] = None


class OverlayData(BaseModel):
    id: int
    name: str
    config: Any


class CreateOverlayResponse(BaseModel):
    success: bool
    data: Optional[OverlayData] = None
    error: Optional[str] = None


class CreateOverlayPlugin(BasePlugin):
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays", "POST", self.execute,
            tags=["Overlays"],
            request_model=CreateOverlayRequest,
            response_model=CreateOverlayResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = CreateOverlayRequest(**data)
            config_json = json.dumps(req.config if req.config is not None else {"elements": []})

            overlay_id = await self.db.execute(
                "INSERT INTO overlays (name, config) VALUES ($1, $2) RETURNING id",
                [req.name, config_json],
            )
            return {
                "success": True,
                "data": {
                    "id": overlay_id,
                    "name": req.name,
                    "config": json.loads(config_json),
                },
            }
        except Exception as e:
            self.logger.error(f"[CreateOverlay] {e}")
            return {"success": False, "error": str(e)}
