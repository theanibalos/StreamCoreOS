from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin

class UpdateTimerRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    message: Optional[str] = Field(None, min_length=1)
    interval_minutes: Optional[int] = Field(None, gt=0)
    min_lines: Optional[int] = Field(None, ge=0)
    enabled: Optional[int] = Field(None, ge=0, le=1)

class TimerData(BaseModel):
    id: int
    name: str
    message: str
    interval_minutes: int
    min_lines: int
    enabled: int

class UpdateTimerResponse(BaseModel):
    success: bool
    data: Optional[TimerData] = None
    error: Optional[str] = None

class UpdateTimerPlugin(BasePlugin):
    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/timers/{id}", "PUT", self.execute,
            tags=["Timers"],
            request_model=UpdateTimerRequest,
            response_model=UpdateTimerResponse
        )

    async def execute(self, data: dict, context=None):
        try:
            timer_id = data.get("id")
            req = UpdateTimerRequest(**data)
            
            # Build dynamic update query
            updates = []
            params = []
            
            if req.name is not None:
                updates.append(f"name = ${len(params)+1}")
                params.append(req.name)
            if req.message is not None:
                updates.append(f"message = ${len(params)+1}")
                params.append(req.message)
            if req.interval_minutes is not None:
                updates.append(f"interval_minutes = ${len(params)+1}")
                params.append(req.interval_minutes)
            if req.min_lines is not None:
                updates.append(f"min_lines = ${len(params)+1}")
                params.append(req.min_lines)
            if req.enabled is not None:
                updates.append(f"enabled = ${len(params)+1}")
                params.append(req.enabled)

            if not updates:
                return {"success": False, "error": "No fields to update"}

            params.append(timer_id)
            await self.db.execute(
                f"UPDATE timers SET {', '.join(updates)} WHERE id = ${len(params)}",
                params
            )
            
            updated_timer = await self.db.query_one("SELECT * FROM timers WHERE id = $1", [timer_id])
            
            if not updated_timer:
                return {"success": False, "error": "Timer not found"}
                
            await self.bus.publish("timer.updated", {"id": timer_id})
            
            return {"success": True, "data": updated_timer}
        except Exception as e:
            self.logger.error(f"[UpdateTimer] Failed: {e}")
            return {"success": False, "error": str(e)}
