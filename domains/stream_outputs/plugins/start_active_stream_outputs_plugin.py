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


class StartActiveStreamOutputsResponse(BaseModel):
    success: bool
    data: Optional[list[StreamOutputData]] = None
    error: Optional[str] = None


class StartActiveStreamOutputsPlugin(BasePlugin):
    """POST /stream-outputs/start-active — Start all enabled outputs via stream_tool."""

    def __init__(self, http, stream_tool, logger):
        self.http = http
        self.stream_tool = stream_tool
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/stream-outputs/start-active", "POST", self.execute,
            tags=["Stream Outputs"], response_model=StartActiveStreamOutputsResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            return {"success": True, "data": await self.stream_tool.start_active_outputs()}
        except Exception as e:
            self.logger.error(f"[StartActiveStreamOutputs] {e}")
            return {"success": False, "error": str(e)}
