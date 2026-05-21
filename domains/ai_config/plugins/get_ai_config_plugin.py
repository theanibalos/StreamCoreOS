from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class AIConfigData(BaseModel):
    provider:           str
    endpoint_url:       str
    model:              str
    has_api_key:        bool
    timeout_s:          int   = 120
    disable_reasoning:  bool  = False
    extra_headers:      dict  = {}
    extra_payload:      dict  = {}
    chat_cooldown_s:    int   = 120
    chat_system_prompt: str   = ""
    chat_max_tokens:    int   = 200
    chat_temperature:   float = 0.7
    updated_at:         Optional[str] = None


class GetAIConfigResponse(BaseModel):
    success: bool
    data:    Optional[AIConfigData] = None
    error:   Optional[str] = None


class GetAIConfigPlugin(BasePlugin):
    """GET /ai/config — Returns current AI config (api_key is never exposed)."""

    def __init__(self, http, ai, logger):
        self.http = http
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/ai/config", "GET", self.execute,
            tags=["AI Config"],
            response_model=GetAIConfigResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            config = self.ai.get_config()
            if not config:
                return {"success": True, "data": None}
            return {"success": True, "data": config}
        except Exception as e:
            self.logger.error(f"[GetAIConfig] {e}")
            return {"success": False, "error": str(e)}
