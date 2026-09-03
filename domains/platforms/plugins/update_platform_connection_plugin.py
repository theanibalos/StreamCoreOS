import json
from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin


class UpdatePlatformConnectionRequest(BaseModel):
    enabled: Optional[bool] = Field(default=None)
    chat_read_enabled: Optional[bool] = Field(default=None)
    chat_write_enabled: Optional[bool] = Field(default=None)
    moderation_enabled: Optional[bool] = Field(default=None)
    capabilities: Optional[dict] = Field(default=None)


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


class UpdatePlatformConnectionResponse(BaseModel):
    success: bool
    data: Optional[PlatformConnectionData] = None
    error: Optional[str] = None


class UpdatePlatformConnectionPlugin(BasePlugin):
    """PUT /platforms/connections/{id} — Update platform connection flags."""

    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/platforms/connections/{id}", "PUT", self.execute,
            tags=["Platforms"],
            request_model=UpdatePlatformConnectionRequest,
            response_model=UpdatePlatformConnectionResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            connection_id = data.get("id")
            req = UpdatePlatformConnectionRequest(**{k: v for k, v in data.items() if k != "id"})

            updates = []
            params = []
            for field in ("enabled", "chat_read_enabled", "chat_write_enabled", "moderation_enabled"):
                value = getattr(req, field)
                if value is not None:
                    updates.append(f"{field} = ${len(params) + 1}")
                    params.append(1 if value else 0)
            if req.capabilities is not None:
                updates.append(f"capabilities = ${len(params) + 1}")
                params.append(json.dumps(req.capabilities))

            if not updates:
                return {"success": False, "error": "No fields to update"}

            updates.append("updated_at = datetime('now')")
            params.append(connection_id)
            await self.db.execute(
                f"UPDATE platform_connections SET {', '.join(updates)} WHERE id = ${len(params)}",
                params,
            )

            row = await self.db.query_one("SELECT * FROM platform_connections WHERE id=$1", [connection_id])
            if not row:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Platform connection not found"}

            result = self._serialize(row)
            await self.bus.publish("platform.connection.updated", result)
            return {"success": True, "data": result}
        except Exception as e:
            self.logger.error(f"[UpdatePlatformConnection] {e}")
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
