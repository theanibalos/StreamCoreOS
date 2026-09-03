from typing import Optional, List
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin

class WebhookEntry(BaseModel):
    id: int
    name: str
    url: str
    method: str
    trigger_type: str
    trigger_value: str
    filter_field: Optional[str] = None
    filter_value: Optional[str] = None
    body_template: Optional[str] = None
    enabled: bool

class ListWebhooksResponse(BaseModel):
    success: bool
    data: Optional[List[WebhookEntry]] = None
    error: Optional[str] = None

class ListWebhooksPlugin(BasePlugin):
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/webhooks", "GET", self.execute,
            tags=["Webhooks"],
            response_model=ListWebhooksResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rows = await self.db.query("SELECT * FROM webhooks ORDER BY created_at DESC")
            entries = []
            for r in rows:
                entries.append({
                    "id": r["id"],
                    "name": r["name"],
                    "url": r["url"],
                    "method": r["method"],
                    "trigger_type": r["trigger_type"],
                    "trigger_value": r["trigger_value"],
                    "filter_field": r.get("filter_field"),
                    "filter_value": r.get("filter_value"),
                    "body_template": r.get("body_template"),
                    "enabled": bool(r["enabled"])
                })
            return {"success": True, "data": entries}
        except Exception as e:
            self.logger.error(f"Failed to list webhooks: {e}")
            return {"success": False, "error": str(e)}
