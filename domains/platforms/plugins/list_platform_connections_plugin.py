import json
from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class PlatformConnectionData(BaseModel):
    id: int
    platform: str
    channel_id: str
    channel_name: str
    enabled: bool
    chat_read_enabled: bool
    chat_write_enabled: bool
    moderation_enabled: bool
    capabilities: dict
    created_at: str
    updated_at: str


class ListPlatformConnectionsResponse(BaseModel):
    success: bool
    data: Optional[list[PlatformConnectionData]] = None
    error: Optional[str] = None


class ListPlatformConnectionsPlugin(BasePlugin):
    """GET /platforms/connections — List connected platform channels."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/platforms/connections", "GET", self.execute,
            tags=["Platforms"],
            response_model=ListPlatformConnectionsResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rows = await self.db.query(
                "SELECT * FROM platform_connections ORDER BY platform, channel_name"
            )
            return {"success": True, "data": [self._serialize(row) for row in rows]}
        except Exception as e:
            self.logger.error(f"[ListPlatformConnections] {e}")
            return {"success": False, "error": str(e)}

    def _serialize(self, row: dict) -> dict:
        return {
            **row,
            "enabled": bool(row["enabled"]),
            "chat_read_enabled": bool(row["chat_read_enabled"]),
            "chat_write_enabled": bool(row["chat_write_enabled"]),
            "moderation_enabled": bool(row["moderation_enabled"]),
            "capabilities": json.loads(row.get("capabilities") or "{}"),
        }
