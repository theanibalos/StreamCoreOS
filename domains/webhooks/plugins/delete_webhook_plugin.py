from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin

class DeleteWebhookResponse(BaseModel):
    success: bool
    error: Optional[str] = None

class DeleteWebhookPlugin(BasePlugin):
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/webhooks/{webhook_id}", "DELETE", self.execute,
            tags=["Webhooks"],
            response_model=DeleteWebhookResponse,
        )

    async def execute(self, data: dict, context=None):
        webhook_id = data.get("webhook_id")
        try:
            await self.db.execute("DELETE FROM webhooks WHERE id = $1", [webhook_id])
            return {"success": True}
        except Exception as e:
            self.logger.error(f"Failed to delete webhook {webhook_id}: {e}")
            return {"success": False, "error": str(e)}
