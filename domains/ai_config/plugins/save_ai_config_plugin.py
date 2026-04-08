from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


_DEFAULT_PROMPT = "You are a helpful Twitch chat assistant. Be concise and reply in under 40 words."


class SaveAIConfigRequest(BaseModel):
    provider:           str   = Field(min_length=1, max_length=50)
    endpoint_url:       str   = Field(min_length=1, max_length=500)
    model:              str   = Field(min_length=1, max_length=100)
    api_key:            str   = Field(default="", max_length=500)
    chat_cooldown_s:    int   = Field(default=120, ge=0, le=86400)
    chat_system_prompt: str   = Field(default=_DEFAULT_PROMPT, max_length=4000)
    chat_max_tokens:    int   = Field(default=200, ge=10, le=2000)
    chat_temperature:   float = Field(default=0.7, ge=0.0, le=2.0)


class AIConfigData(BaseModel):
    provider:           str
    endpoint_url:       str
    model:              str
    has_api_key:        bool
    chat_cooldown_s:    int
    chat_system_prompt: str
    chat_max_tokens:    int
    chat_temperature:   float
    updated_at:         Optional[str] = None


class SaveAIConfigResponse(BaseModel):
    success: bool
    data:    Optional[AIConfigData] = None
    error:   Optional[str] = None


class SaveAIConfigPlugin(BasePlugin):
    """
    PUT /ai/config — Upserts the AI provider configuration.

    Keeps a single row (id=1). After saving, reloads the AI tool cache
    so changes take effect immediately without a restart.
    """

    def __init__(self, http, db, ai, logger):
        self.http = http
        self.db = db
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/ai/config", "PUT", self.execute,
            tags=["AI Config"],
            request_model=SaveAIConfigRequest,
            response_model=SaveAIConfigResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = SaveAIConfigRequest(**data)

            existing = await self.db.query_one("SELECT id FROM ai_config WHERE id = 1")

            if existing:
                await self.db.execute(
                    """UPDATE ai_config
                       SET provider=$1, endpoint_url=$2, model=$3, api_key=$4,
                           chat_cooldown_s=$5, chat_system_prompt=$6,
                           chat_max_tokens=$7, chat_temperature=$8,
                           updated_at=datetime('now')
                       WHERE id=1""",
                    [req.provider, req.endpoint_url, req.model, req.api_key,
                     req.chat_cooldown_s, req.chat_system_prompt,
                     req.chat_max_tokens, req.chat_temperature],
                )
            else:
                await self.db.execute(
                    """INSERT INTO ai_config
                       (id, provider, endpoint_url, model, api_key, chat_cooldown_s,
                        chat_system_prompt, chat_max_tokens, chat_temperature)
                       VALUES (1, $1, $2, $3, $4, $5, $6, $7, $8)""",
                    [req.provider, req.endpoint_url, req.model, req.api_key,
                     req.chat_cooldown_s, req.chat_system_prompt,
                     req.chat_max_tokens, req.chat_temperature],
                )

            # Reload tool cache so plugins use the new config immediately
            await self.ai.reload_config()

            self.logger.info(f"[AIConfig] Updated — provider={req.provider} model={req.model}")

            return {
                "success": True,
                "data": {
                    "provider":           req.provider,
                    "endpoint_url":       req.endpoint_url,
                    "model":              req.model,
                    "has_api_key":        bool(req.api_key),
                    "chat_cooldown_s":    req.chat_cooldown_s,
                    "chat_system_prompt": req.chat_system_prompt,
                    "chat_max_tokens":    req.chat_max_tokens,
                    "chat_temperature":   req.chat_temperature,
                    "updated_at":         None,
                },
            }
        except Exception as e:
            self.logger.error(f"[SaveAIConfig] {e}")
            return {"success": False, "error": str(e)}
