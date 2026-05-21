from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin

class DeleteTimerResponse(BaseModel):
    success: bool
    error: Optional[str] = None

class DeleteTimerPlugin(BasePlugin):
    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/timers/{id}", "DELETE", self.execute,
            tags=["Timers"],
            response_model=DeleteTimerResponse
        )

    async def execute(self, data: dict, context=None):
        try:
            timer_id = data.get("id")
            deleted = await self.db.execute("DELETE FROM timers WHERE id = $1", [timer_id])
            
            if not deleted:
                return {"success": False, "error": "Timer not found"}
                
            await self.bus.publish("timer.deleted", {"id": timer_id})
            
            return {"success": True}
        except Exception as e:
            self.logger.error(f"[DeleteTimer] Failed: {e}")
            return {"success": False, "error": str(e)}
