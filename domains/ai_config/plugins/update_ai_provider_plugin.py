import json
from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin


class UpdateAIProviderRequest(BaseModel):
    name:              str   = Field(min_length=1, max_length=100)
    provider:          str   = Field(min_length=1, max_length=50)
    endpoint_url:      str   = Field(min_length=1, max_length=500)
    model:             str   = Field(min_length=1, max_length=100)
    api_key:           str   = Field(default="", max_length=500)
    timeout_s:         int   = Field(default=120, ge=5, le=600)
    disable_reasoning: bool  = Field(default=False)
    extra_headers:     dict  = Field(default_factory=dict)
    extra_payload:     dict  = Field(default_factory=dict)


class UpdateAIProviderResponse(BaseModel):
    success: bool
    error:   Optional[str] = None


class UpdateAIProviderPlugin(BasePlugin):
    """
    PUT /api/ai/providers/{provider_id} — Updates a saved AI provider.

    If the provider being edited is the currently active one, the merged
    (provider + chat) config is re-pushed into AITool immediately.
    """

    def __init__(self, http, db, ai, logger):
        self.http = http
        self.db = db
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/ai/providers/{provider_id}", "PUT", self.execute,
            tags=["AI Config"],
            response_model=UpdateAIProviderResponse,
        )

    async def execute(self, data: dict, context=None):
        raw_id = data.get("provider_id")
        if raw_id is None:
            return {"success": False, "error": "Missing provider_id"}
        try:
            provider_id = int(raw_id)
            body = {k: v for k, v in data.items() if k != "provider_id"}
            req = UpdateAIProviderRequest(**body)

            existing_key_row = await self.db.query_one(
                "SELECT api_key FROM ai_providers WHERE id = $1", [provider_id]
            )
            if not existing_key_row:
                return {"success": False, "error": "Provider not found."}

            api_key = req.api_key or existing_key_row["api_key"]

            await self.db.execute(
                """UPDATE ai_providers
                   SET name=$1, provider=$2, endpoint_url=$3, api_key=$4, model=$5,
                       timeout_s=$6, disable_reasoning=$7, extra_headers=$8, extra_payload=$9,
                       updated_at=datetime('now')
                   WHERE id=$10""",
                [
                    req.name, req.provider, req.endpoint_url, api_key, req.model,
                    req.timeout_s, int(req.disable_reasoning),
                    json.dumps(req.extra_headers), json.dumps(req.extra_payload),
                    provider_id,
                ],
            )

            cfg = await self.db.query_one("SELECT active_provider_id FROM ai_config WHERE id = 1")
            if cfg and cfg["active_provider_id"] == provider_id:
                chat = await self.db.query_one(
                    "SELECT chat_cooldown_s, chat_system_prompt, chat_max_tokens, chat_temperature "
                    "FROM ai_config WHERE id = 1"
                ) or {}
                self.ai.load_config({
                    "provider":           req.provider,
                    "endpoint_url":       req.endpoint_url,
                    "model":              req.model,
                    "api_key":            api_key,
                    "timeout_s":          req.timeout_s,
                    "disable_reasoning":  req.disable_reasoning,
                    "extra_headers":      req.extra_headers,
                    "extra_payload":      req.extra_payload,
                    "chat_cooldown_s":    chat.get("chat_cooldown_s", 120),
                    "chat_system_prompt": chat.get("chat_system_prompt", ""),
                    "chat_max_tokens":    chat.get("chat_max_tokens", 200),
                    "chat_temperature":   chat.get("chat_temperature", 0.7),
                })

            self.logger.info(f"[AIProviders] Updated — id={provider_id} name={req.name}")
            return {"success": True}
        except Exception as e:
            self.logger.error(f"[UpdateAIProvider] {e}")
            return {"success": False, "error": str(e)}
