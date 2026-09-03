import json
from typing import Optional
from pydantic import BaseModel
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


class ListStreamOutputsResponse(BaseModel):
    success: bool
    data: Optional[list[StreamOutputData]] = None
    error: Optional[str] = None


class ListStreamOutputsPlugin(BasePlugin):
    """GET /stream-outputs — List logical/restream outputs."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/stream-outputs", "GET", self.execute,
            tags=["Stream Outputs"],
            response_model=ListStreamOutputsResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rows = await self.db.query("SELECT * FROM stream_outputs ORDER BY created_at ASC, id ASC")
            return {"success": True, "data": [serialize_stream_output(row) for row in rows]}
        except Exception as e:
            self.logger.error(f"[ListStreamOutputs] {e}")
            return {"success": False, "error": str(e)}
