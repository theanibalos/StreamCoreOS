from typing import Optional, List
from pydantic import BaseModel
from core.base_plugin import BasePlugin

class CreateWebhookRequest(BaseModel):
    name: str
    url: str
    method: str = "POST"
    headers: Optional[str] = None
    body_template: Optional[str] = None
    trigger_type: str
    trigger_value: str
    filter_field: Optional[str] = None
    filter_value: Optional[str] = None
    enabled: bool = True

class WebhookData(BaseModel):
    id: int
    name: str
    url: str
    trigger_type: str
    trigger_value: str
    enabled: bool

class CreateWebhookResponse(BaseModel):
    success: bool
    data: Optional[WebhookData] = None
    error: Optional[str] = None

class CreateWebhookPlugin(BasePlugin):
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/webhooks", "POST", self.execute,
            tags=["Webhooks"], request_model=CreateWebhookRequest,
            response_model=CreateWebhookResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            # Convert empty strings to None for optional fields
            processed_data = {
                k: (None if v == "" else v) 
                for k, v in data.items()
            }
            req = CreateWebhookRequest(**processed_data)
            
            new_id = await self.db.execute(
                """INSERT INTO webhooks (name, url, method, headers, body_template, trigger_type, trigger_value, filter_field, filter_value, enabled)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                [req.name, req.url, req.method, req.headers, req.body_template, req.trigger_type, req.trigger_value, req.filter_field, req.filter_value, 1 if req.enabled else 0]
            )
            return {
                "success": True, 
                "data": {
                    "id": new_id, 
                    "name": req.name, 
                    "url": req.url, 
                    "trigger_type": req.trigger_type, 
                    "trigger_value": req.trigger_value,
                    "enabled": req.enabled
                }
            }
        except Exception as e:
            self.logger.error(f"Failed to create webhook: {e}")
            return {"success": False, "error": str(e)}
