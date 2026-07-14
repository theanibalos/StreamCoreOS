import json
from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class CreateAIProviderRequest(BaseModel):
    name:              str   = Field(min_length=1, max_length=100)
    provider:          str   = Field(min_length=1, max_length=50)
    endpoint_url:      str   = Field(min_length=1, max_length=500)
    model:             str   = Field(min_length=1, max_length=100)
    api_key:           str   = Field(default="", max_length=500)
    timeout_s:         int   = Field(default=120, ge=5, le=600)
    disable_reasoning: bool  = Field(default=False)
    extra_headers:     dict  = Field(default_factory=dict)
    extra_payload:     dict  = Field(default_factory=dict)


class AIProviderData(BaseModel):
    id:                int
    name:              str
    provider:          str
    endpoint_url:      str
    model:             str
    has_api_key:       bool
    timeout_s:         int
    disable_reasoning: bool
    extra_headers:     dict
    extra_payload:     dict
    is_active:         bool
    updated_at:        str


class CreateAIProviderResponse(BaseModel):
    success: bool
    data:    Optional[AIProviderData] = None
    error:   Optional[str] = None


class CreateAIProviderPlugin(BasePlugin):
    """
    POST /api/ai/providers — Saves a new AI provider.

    If this is the first provider ever saved, it's automatically activated
    (pushed into the live AITool) so the app is usable right after one save.
    """

    def __init__(self, http, db, ai, logger):
        self.http = http
        self.db = db
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/ai/providers", "POST", self.execute,
            tags=["AI Config"],
            request_model=CreateAIProviderRequest,
            response_model=CreateAIProviderResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = CreateAIProviderRequest(**data)

            existing_count = await self.db.query_one("SELECT COUNT(*) AS n FROM ai_providers")
            is_first = not existing_count or existing_count["n"] == 0

            new_id = await self.db.execute(
                """INSERT INTO ai_providers
                   (name, provider, endpoint_url, api_key, model,
                    timeout_s, disable_reasoning, extra_headers, extra_payload)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                [
                    req.name, req.provider, req.endpoint_url, req.api_key, req.model,
                    req.timeout_s, int(req.disable_reasoning),
                    json.dumps(req.extra_headers), json.dumps(req.extra_payload),
                ],
            )

            if is_first:
                await self.db.execute(
                    """INSERT INTO ai_config (id, active_provider_id) VALUES (1, $1)
                       ON CONFLICT(id) DO UPDATE SET active_provider_id = $1""",
                    [new_id],
                )
                chat = await self.db.query_one(
                    "SELECT chat_cooldown_s, chat_system_prompt, chat_max_tokens, chat_temperature "
                    "FROM ai_config WHERE id = 1"
                ) or {}
                self.ai.load_config({
                    "provider":           req.provider,
                    "endpoint_url":       req.endpoint_url,
                    "model":              req.model,
                    "api_key":            req.api_key,
                    "timeout_s":          req.timeout_s,
                    "disable_reasoning":  req.disable_reasoning,
                    "extra_headers":      req.extra_headers,
                    "extra_payload":      req.extra_payload,
                    "chat_cooldown_s":    chat.get("chat_cooldown_s", 120),
                    "chat_system_prompt": chat.get("chat_system_prompt", ""),
                    "chat_max_tokens":    chat.get("chat_max_tokens", 200),
                    "chat_temperature":   chat.get("chat_temperature", 0.7),
                })

            row = await self.db.query_one("SELECT * FROM ai_providers WHERE id = $1", [new_id])
            self.logger.info(f"[AIProviders] Created — id={new_id} name={req.name} provider={req.provider}")

            return {
                "success": True,
                "data": {
                    "id":                row["id"],
                    "name":              row["name"],
                    "provider":          row["provider"],
                    "endpoint_url":      row["endpoint_url"],
                    "model":             row["model"],
                    "has_api_key":       bool(row["api_key"]),
                    "timeout_s":         row["timeout_s"],
                    "disable_reasoning": bool(row["disable_reasoning"]),
                    "extra_headers":     req.extra_headers,
                    "extra_payload":     req.extra_payload,
                    "is_active":         is_first,
                    "updated_at":        row["updated_at"],
                },
            }
        except Exception as e:
            self.logger.error(f"[CreateAIProvider] {e}")
            return {"success": False, "error": str(e)}
