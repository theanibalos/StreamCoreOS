from typing import List, Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin

class TimerData(BaseModel):
    id: int
    name: str
    message: str
    interval_minutes: int
    min_lines: int
    enabled: int
    last_executed_at: Optional[str] = None

class GetTimersResponse(BaseModel):
    success: bool
    data: List[TimerData] = []
    error: Optional[str] = None

class GetTimersPlugin(BasePlugin):
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/timers", "GET", self.execute,
            tags=["Timers"],
            response_model=GetTimersResponse
        )

    async def execute(self, data: dict, context=None):
        try:
            timers = await self.db.query("SELECT * FROM timers ORDER BY created_at DESC")
            return {"success": True, "data": timers}
        except Exception as e:
            self.logger.error(f"[GetTimers] Failed: {e}")
            return {"success": False, "error": str(e)}
