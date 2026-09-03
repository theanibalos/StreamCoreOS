from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class StreamRuntimeStatusData(BaseModel):
    input_url: str
    obs_url: str
    obs_stream_key: str
    obs_connected: bool
    ffmpeg_available: bool
    rtmp_engine_available: bool
    relays: dict
    relays_count: int
    live_outputs_count: int
    enabled_outputs_count: Optional[int] = 0
    is_transmitting: Optional[bool] = False
    active_source: str
    fallback_running: bool
    fallback_mode: str
    fallback_video_configured: bool
    fallback_video_path: Optional[str] = None
    fallback_video_url: Optional[str] = None


class StreamRuntimeStatusResponse(BaseModel):
    success: bool
    data: Optional[StreamRuntimeStatusData] = None
    error: Optional[str] = None


class StreamRuntimeStatusPlugin(BasePlugin):
    """GET /stream-outputs/runtime/status — OBS ingest and relay runtime state."""

    def __init__(self, http, stream_tool, logger):
        self.http = http
        self.stream_tool = stream_tool
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/stream-outputs/runtime/status", "GET", self.execute,
            tags=["Stream Outputs"], response_model=StreamRuntimeStatusResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            return {"success": True, "data": await self.stream_tool.get_runtime_status()}
        except Exception as e:
            self.logger.error(f"[StreamRuntimeStatus] {e}")
            return {"success": False, "error": str(e)}
