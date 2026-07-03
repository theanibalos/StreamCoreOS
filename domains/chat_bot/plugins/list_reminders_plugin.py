from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class ReminderData(BaseModel):
    job_id: str
    message: str
    run_at: str
    scheduled_by: str
    channel: str


class ListRemindersResponse(BaseModel):
    success: bool
    data: Optional[list[ReminderData]] = None
    error: Optional[str] = None


class ListRemindersPlugin(BasePlugin):
    """GET /chat/reminders — List active scheduled echo/reminder messages."""

    def __init__(self, http, state, logger):
        self.http = http
        self.state = state
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/chat/reminders", "GET", self.execute,
            tags=["Chat"],
            response_model=ListRemindersResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            active = await self.state.get("active_reminders", {}, namespace="echo")
            reminders = [
                {"job_id": job_id, **info}
                for job_id, info in active.items()
            ]
            reminders.sort(key=lambda r: r["run_at"])
            return {"success": True, "data": reminders}
        except Exception as e:
            self.logger.error(f"[ListReminders] {e}")
            return {"success": False, "error": str(e)}
