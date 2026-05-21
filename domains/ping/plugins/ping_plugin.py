from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


# ── Response schema ──────────────────────────────────────────────────────────
class PingData(BaseModel):
    status: str
    message: str


class PingResponse(BaseModel):
    success: bool
    data: Optional[PingData] = None
    error: Optional[str] = None


class PingPlugin(BasePlugin):
    """
    A simple health-check plugin to verify the MicroCoreOS kernel is alive.
    """
    def __init__(self, logger, http):
        self.logger = logger
        self.http = http

    async def on_boot(self):
        self.http.add_endpoint(
            path="/api/ping",
            method="GET",
            handler=self.execute,
            tags=["System"],
            response_model=PingResponse,
        )

    async def execute(self, data: dict = None, context=None):
        return {"success": True, "data": {"status": "ok", "message": "pong"}}
