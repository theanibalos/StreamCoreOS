import json
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


_DEFAULT_PROMPT = "You are a helpful Twitch chat assistant. Be concise and reply in under 40 words."


class SaveAIConfigRequest(BaseModel):
    provider:           str   = Field(min_length=1, max_length=50)
    endpoint_url:       str   = Field(min_length=1, max_length=500)
    model:              str   = Field(min_length=1, max_length=100)
    api_key:            str   = Field(default="", max_length=500)
    timeout_s:          int   = Field(default=120, ge=5, le=600)
    disable_reasoning:  bool  = Field(default=False)
    # JSON dicts stored as strings in SQLite
    extra_headers:      dict  = Field(default_factory=dict)
    extra_payload:      dict  = Field(default_factory=dict)
    chat_cooldown_s:    int   = Field(default=120, ge=0, le=86400)
    chat_system_prompt: str   = Field(default=_DEFAULT_PROMPT, max_length=4000)
    chat_max_tokens:    int   = Field(default=200, ge=10, le=2000)
    chat_temperature:   float = Field(default=0.7, ge=0.0, le=2.0)


class AIConfigData(BaseModel):
    provider:           str
    endpoint_url:       str
    model:              str
    has_api_key:        bool
    timeout_s:          int
    disable_reasoning:  bool
    extra_headers:      dict
    extra_payload:      dict
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

    Keeps a single row (id=1). After saving, pushes the new config into the
    AI tool so changes take effect immediately without restart.
    """

    def __init__(self, http, db, ai, logger):
        self.http = http
        self.db = db
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/ai/config", "PUT", self.execute,
            tags=["AI Config"],
            request_model=SaveAIConfigRequest,
            response_model=SaveAIConfigResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = SaveAIConfigRequest(**data)

            extra_headers_str = json.dumps(req.extra_headers)
            extra_payload_str = json.dumps(req.extra_payload)

            existing = await self.db.query_one("SELECT id FROM ai_config WHERE id = 1")

            if existing:
                await self.db.execute(
                    """UPDATE ai_config
                       SET provider=$1, endpoint_url=$2, model=$3, api_key=$4,
                           timeout_s=$5, disable_reasoning=$6,
                           extra_headers=$7, extra_payload=$8,
                           chat_cooldown_s=$9, chat_system_prompt=$10,
                           chat_max_tokens=$11, chat_temperature=$12,
                           updated_at=datetime('now')
                       WHERE id=1""",
                    [
                        req.provider, req.endpoint_url, req.model, req.api_key,
                        req.timeout_s, int(req.disable_reasoning),
                        extra_headers_str, extra_payload_str,
                        req.chat_cooldown_s, req.chat_system_prompt,
                        req.chat_max_tokens, req.chat_temperature,
                    ],
                )
            else:
                await self.db.execute(
                    """INSERT INTO ai_config
                       (id, provider, endpoint_url, model, api_key,
                        timeout_s, disable_reasoning, extra_headers, extra_payload,
                        chat_cooldown_s, chat_system_prompt, chat_max_tokens, chat_temperature)
                       VALUES (1, $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                    [
                        req.provider, req.endpoint_url, req.model, req.api_key,
                        req.timeout_s, int(req.disable_reasoning),
                        extra_headers_str, extra_payload_str,
                        req.chat_cooldown_s, req.chat_system_prompt,
                        req.chat_max_tokens, req.chat_temperature,
                    ],
                )

            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            self.ai.load_config({
                "provider":           req.provider,
                "endpoint_url":       req.endpoint_url,
                "model":              req.model,
                "api_key":            req.api_key,
                "timeout_s":          req.timeout_s,
                "disable_reasoning":  req.disable_reasoning,
                "extra_headers":      req.extra_headers,
                "extra_payload":      req.extra_payload,
                "chat_cooldown_s":    req.chat_cooldown_s,
                "chat_system_prompt": req.chat_system_prompt,
                "chat_max_tokens":    req.chat_max_tokens,
                "chat_temperature":   req.chat_temperature,
                "updated_at":         now,
            })

            self.logger.info(f"[AIConfig] Updated — provider={req.provider} model={req.model}")

            return {
                "success": True,
                "data": {
                    "provider":           req.provider,
                    "endpoint_url":       req.endpoint_url,
                    "model":              req.model,
                    "has_api_key":        bool(req.api_key),
                    "timeout_s":          req.timeout_s,
                    "disable_reasoning":  req.disable_reasoning,
                    "extra_headers":      req.extra_headers,
                    "extra_payload":      req.extra_payload,
                    "chat_cooldown_s":    req.chat_cooldown_s,
                    "chat_system_prompt": req.chat_system_prompt,
                    "chat_max_tokens":    req.chat_max_tokens,
                    "chat_temperature":   req.chat_temperature,
                    "updated_at":         now,
                },
            }
        except Exception as e:
            self.logger.error(f"[SaveAIConfig] {e}")
            return {"success": False, "error": str(e)}
