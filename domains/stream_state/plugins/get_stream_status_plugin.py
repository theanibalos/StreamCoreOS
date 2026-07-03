from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class StreamStatusData(BaseModel):
    online: bool
    session_id: Optional[int] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    broadcaster_login: Optional[str] = None


class StreamStatusResponse(BaseModel):
    success: bool
    data: Optional[StreamStatusData] = None
    error: Optional[str] = None


class GetStreamStatusPlugin(BasePlugin):
    """
    GET /stream/status

    Returns the current stream state directly from the in-memory state tool
    (no DB query — always fast).
    """

    def __init__(self, http, state, logger):
        self.http = http
        self.state = state
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/stream/status",
            "GET",
            self.execute,
            tags=["Stream"],
            response_model=StreamStatusResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            online = await self.state.get("online", default=False, namespace="stream_state")
            status_data = StreamStatusData(
                online=online,
                session_id=await self.state.get("session_id", namespace="stream_state"),
                started_at=await self.state.get("started_at", namespace="stream_state"),
                ended_at=await self.state.get("ended_at", namespace="stream_state"),
                broadcaster_login=await self.state.get("broadcaster_login", namespace="stream_state"),
            )
            return {
                "success": True,
                "data": status_data.model_dump(),
            }
        except Exception as e:
            self.logger.error(f"[GetStreamStatus] {e}")
            return {"success": False, "error": str(e)}
