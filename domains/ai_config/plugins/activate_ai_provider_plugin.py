import json
from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class ActivateAIProviderResponse(BaseModel):
    success: bool
    error:   Optional[str] = None


class ActivateAIProviderPlugin(BasePlugin):
    """
    POST /api/ai/providers/{provider_id}/activate — Marks a saved provider as
    the one in use. Merges it with the current chat-personality settings and
    pushes the result into AITool immediately.
    """

    def __init__(self, http, db, ai, logger):
        self.http = http
        self.db = db
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/ai/providers/{provider_id}/activate", "POST", self.execute,
            tags=["AI Config"],
            response_model=ActivateAIProviderResponse,
        )

    async def execute(self, data: dict, context=None):
        raw_id = data.get("provider_id")
        if raw_id is None:
            return {"success": False, "error": "Missing provider_id"}
        try:
            provider_id = int(raw_id)

            provider = await self.db.query_one("SELECT * FROM ai_providers WHERE id = $1", [provider_id])
            if not provider:
                return {"success": False, "error": "Provider not found."}

            await self.db.execute(
                """INSERT INTO ai_config (id, active_provider_id) VALUES (1, $1)
                   ON CONFLICT(id) DO UPDATE SET active_provider_id = $1""",
                [provider_id],
            )

            chat = await self.db.query_one(
                "SELECT chat_cooldown_s, chat_system_prompt, chat_max_tokens, chat_temperature "
                "FROM ai_config WHERE id = 1"
            ) or {}

            self.ai.load_config({
                "provider":           provider["provider"],
                "endpoint_url":       provider["endpoint_url"],
                "model":              provider["model"],
                "api_key":            provider["api_key"],
                "timeout_s":          provider["timeout_s"],
                "disable_reasoning":  bool(provider["disable_reasoning"]),
                "extra_headers":      self._load_json(provider.get("extra_headers")),
                "extra_payload":      self._load_json(provider.get("extra_payload")),
                "chat_cooldown_s":    chat.get("chat_cooldown_s", 120),
                "chat_system_prompt": chat.get("chat_system_prompt", ""),
                "chat_max_tokens":    chat.get("chat_max_tokens", 200),
                "chat_temperature":   chat.get("chat_temperature", 0.7),
            })

            self.logger.info(f"[AIProviders] Activated — id={provider_id} name={provider['name']}")
            return {"success": True}
        except Exception as e:
            self.logger.error(f"[ActivateAIProvider] {e}")
            return {"success": False, "error": str(e)}

    def _load_json(self, val) -> dict:
        if not val:
            return {}
        if isinstance(val, dict):
            return val
        try:
            return json.loads(val)
        except Exception:
            return {}
