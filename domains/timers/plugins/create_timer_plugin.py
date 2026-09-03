from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin

class CreateTimerRequest(BaseModel):
    name: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    interval_minutes: int = Field(..., gt=0)
    min_lines: int = Field(default=0, ge=0)
    enabled: int = Field(default=1)

class TimerData(BaseModel):
    id: int
    name: str
    message: str
    interval_minutes: int
    min_lines: int
    enabled: int

class TimerResponse(BaseModel):
    success: bool
    data: Optional[TimerData] = None
    error: Optional[str] = None

class CreateTimerPlugin(BasePlugin):
    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/timers", "POST", self.execute,
            tags=["Timers"],
            request_model=CreateTimerRequest,
            response_model=TimerResponse
        )

    async def execute(self, data: dict, context=None):
        try:
            req = CreateTimerRequest(**data)
            timer_id = await self.db.execute(
                """INSERT INTO timers (name, message, interval_minutes, min_lines, enabled)
                   VALUES ($1, $2, $3, $4, $5) RETURNING id""",
                [req.name, req.message, req.interval_minutes, req.min_lines, req.enabled]
            )
            
            await self.bus.publish("timer.created", {"id": timer_id, "name": req.name})
            
            return {
                "success": True,
                "data": {
                    "id": timer_id,
                    "name": req.name,
                    "message": req.message,
                    "interval_minutes": req.interval_minutes,
                    "min_lines": req.min_lines,
                    "enabled": req.enabled
                }
            }
        except Exception as e:
            self.logger.error(f"[CreateTimer] Failed: {e}")
            return {"success": False, "error": str(e)}
