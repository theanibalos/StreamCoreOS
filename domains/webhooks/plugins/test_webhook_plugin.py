import json
from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin

class TestWebhookRequest(BaseModel):
    url: str
    method: str = "POST"
    headers: Optional[str] = None
    body_template: Optional[str] = None

class TestWebhookResponse(BaseModel):
    success: bool
    status: Optional[int] = None
    response: Optional[str] = None
    error: Optional[str] = None

class TestWebhookPlugin(BasePlugin):
    def __init__(self, http, http_client, logger):
        self.http = http
        self.http_client = http_client
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/webhooks/test", "POST", self.execute,
            tags=["Webhooks"], request_model=TestWebhookRequest,
            response_model=TestWebhookResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = TestWebhookRequest(**data)
            url = req.url
            method = req.method.upper()
            headers = json.loads(req.headers) if req.headers else {}
            
            # Use some dummy data for the test
            dummy_data = {
                "user_name": "StreamCoreTester",
                "display_name": "StreamCore Tester",
                "test": True,
                "timestamp": "2024-01-01T00:00:00Z"
            }
            
            body = req.body_template
            if body:
                for key, value in dummy_data.items():
                    body = body.replace("{" + key + "}", str(value))
                try:
                    body = json.loads(body)
                except:
                    pass

            self.logger.info(f"[WebhookTest] Testing {method} -> {url}")
            
            if method == "POST":
                # Check if it's JSON or raw
                if isinstance(body, dict):
                    resp = await self.http_client.post(url, json=body, headers=headers)
                else:
                    resp = await self.http_client.post(url, data=body, headers=headers)
            else:
                resp = await self.http_client.get(url, headers=headers)

            return {
                "success": resp["ok"],
                "status": resp["status"],
                "response": resp["text"][:500], # Truncate long responses
                "error": None if resp["ok"] else f"Status {resp['status']}"
            }
        except Exception as e:
            self.logger.error(f"[WebhookTest] Test failed: {e}")
            return {"success": False, "error": str(e)}
