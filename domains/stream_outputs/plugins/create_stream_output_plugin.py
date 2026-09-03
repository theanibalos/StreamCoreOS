import json
from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin
from domains.stream_outputs.plugins.list_stream_outputs_plugin import StreamOutputData, serialize_stream_output


class CreateStreamOutputRequest(BaseModel):
    name: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    enabled: bool = True
    overlay_id: Optional[int] = None
    rtmp_url: Optional[str] = None
    stream_key_secret: Optional[str] = None
    settings: dict = Field(default_factory=dict)


class CreateStreamOutputResponse(BaseModel):
    success: bool
    data: Optional[StreamOutputData] = None
    error: Optional[str] = None


class CreateStreamOutputPlugin(BasePlugin):
    """POST /stream-outputs — Create a Twitch/YouTube/custom stream destination."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/stream-outputs", "POST", self.execute,
            tags=["Stream Outputs"],
            request_model=CreateStreamOutputRequest,
            response_model=CreateStreamOutputResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = CreateStreamOutputRequest(**data)
            row_id = await self.db.execute(
                """
                INSERT INTO stream_outputs
                    (name, platform, channel_id, enabled, overlay_id, rtmp_url, stream_key_secret, settings)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                [
                    req.name.strip(),
                    req.platform.strip().lower(),
                    req.channel_id.strip(),
                    1 if req.enabled else 0,
                    req.overlay_id,
                    req.rtmp_url,
                    req.stream_key_secret,
                    json.dumps(req.settings),
                ],
            )
            row = await self.db.query_one("SELECT * FROM stream_outputs WHERE id=$1", [row_id])
            return {"success": True, "data": serialize_stream_output(row)}
        except Exception as e:
            self.logger.error(f"[CreateStreamOutput] {e}")
            return {"success": False, "error": str(e)}
