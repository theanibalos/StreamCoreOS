import json
from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin


class StreamOutputData(BaseModel):
    id: int
    name: str
    platform: str
    channel_id: str
    enabled: bool
    overlay_id: Optional[int] = None
    rtmp_url: Optional[str] = None
    stream_key_configured: bool
    stream_key_preview: Optional[str] = None
    status: str
    settings: dict
    created_at: str
    updated_at: str


def serialize_stream_output(row: dict) -> dict:
    secret = row.get("stream_key_secret") or ""
    return {
        "id": row["id"],
        "name": row["name"],
        "platform": row["platform"],
        "channel_id": row["channel_id"],
        "enabled": bool(row["enabled"]),
        "overlay_id": row.get("overlay_id"),
        "rtmp_url": row.get("rtmp_url"),
        "stream_key_configured": bool(secret),
        "stream_key_preview": secret[-4:] if secret else None,
        "status": row["status"],
        "settings": json.loads(row.get("settings") or "{}") if isinstance(row.get("settings"), str) else (row.get("settings") or {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class UpdateStreamOutputRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    platform: Optional[str] = Field(default=None, min_length=1)
    channel_id: Optional[str] = Field(default=None, min_length=1)
    enabled: Optional[bool] = Field(default=None)
    overlay_id: Optional[int] = Field(default=None)
    rtmp_url: Optional[str] = Field(default=None)
    stream_key_secret: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
    settings: Optional[dict] = Field(default=None)


class UpdateStreamOutputResponse(BaseModel):
    success: bool
    data: Optional[StreamOutputData] = None
    error: Optional[str] = None


class UpdateStreamOutputPlugin(BasePlugin):
    """PUT /stream-outputs/{id} — Update a stream destination."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/stream-outputs/{id}", "PUT", self.execute,
            tags=["Stream Outputs"],
            request_model=UpdateStreamOutputRequest,
            response_model=UpdateStreamOutputResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            output_id = data.get("id")
            existing = await self.db.query_one("SELECT * FROM stream_outputs WHERE id=$1", [output_id])
            if not existing:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Stream output not found"}

            allowed = {
                "name", "platform", "channel_id", "enabled", "overlay_id", "rtmp_url",
                "stream_key_secret", "status", "settings",
            }
            updates = []
            params = []
            for field in allowed:
                if field not in data:
                    continue
                value = data[field]
                if value is None and field in {"name", "platform", "channel_id", "status"}:
                    continue
                if field == "enabled":
                    value = 1 if value else 0
                elif field == "settings":
                    value = json.dumps(value or {})
                elif field in {"name", "platform", "channel_id"} and isinstance(value, str):
                    value = value.strip().lower() if field == "platform" else value.strip()
                updates.append(f"{field} = ${len(params) + 1}")
                params.append(value)

            if not updates:
                return {"success": False, "error": "No fields to update"}

            updates.append("updated_at = datetime('now')")
            params.append(output_id)
            await self.db.execute(
                f"UPDATE stream_outputs SET {', '.join(updates)} WHERE id = ${len(params)}",
                params,
            )
            row = await self.db.query_one("SELECT * FROM stream_outputs WHERE id=$1", [output_id])
            return {"success": True, "data": serialize_stream_output(row)}
        except Exception as e:
            self.logger.error(f"[UpdateStreamOutput] {e}")
            return {"success": False, "error": str(e)}
