from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


_DEFAULT_PROMPT = "You are a helpful Twitch chat assistant. Be concise and reply in under 40 words."


class SaveAIConfigRequest(BaseModel):
    chat_cooldown_s:    int   = Field(default=120, ge=0, le=86400)
    chat_system_prompt: str   = Field(default=_DEFAULT_PROMPT, max_length=4000)
    chat_max_tokens:    int   = Field(default=200, ge=10, le=2000)
    chat_temperature:   float = Field(default=0.7, ge=0.0, le=2.0)


class AIConfigData(BaseModel):
    provider:           Optional[str] = None
    endpoint_url:       Optional[str] = None
    model:              Optional[str] = None
    has_api_key:        bool = False
    timeout_s:          Optional[int] = None
    disable_reasoning:  Optional[bool] = None
    extra_headers:      dict = {}
    extra_payload:      dict = {}
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
    PUT /ai/config — Upserts the chat-personality settings (system prompt,
    temperature, max tokens, cooldown).

    Provider connection settings (endpoint/api_key/model/...) live in
    ai_providers and are managed via /api/ai/providers* instead — this
    endpoint never touches them, so editing chat settings can't clobber
    whichever provider is currently active.
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

            await self.db.execute(
                """INSERT INTO ai_config
                       (id, chat_cooldown_s, chat_system_prompt, chat_max_tokens, chat_temperature)
                   VALUES (1, $1, $2, $3, $4)
                   ON CONFLICT(id) DO UPDATE SET
                       chat_cooldown_s=$1, chat_system_prompt=$2,
                       chat_max_tokens=$3, chat_temperature=$4,
                       updated_at=datetime('now')""",
                [req.chat_cooldown_s, req.chat_system_prompt, req.chat_max_tokens, req.chat_temperature],
            )

            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            self.ai.patch_config({
                "chat_cooldown_s":    req.chat_cooldown_s,
                "chat_system_prompt": req.chat_system_prompt,
                "chat_max_tokens":    req.chat_max_tokens,
                "chat_temperature":   req.chat_temperature,
                "updated_at":         now,
            })

            self.logger.info("[AIConfig] Chat settings updated.")

            merged = self.ai.get_config() or {
                "has_api_key": False, "extra_headers": {}, "extra_payload": {},
                "chat_cooldown_s": req.chat_cooldown_s,
                "chat_system_prompt": req.chat_system_prompt,
                "chat_max_tokens": req.chat_max_tokens,
                "chat_temperature": req.chat_temperature,
                "updated_at": now,
            }

            return {"success": True, "data": merged}
        except Exception as e:
            self.logger.error(f"[SaveAIConfig] {e}")
            return {"success": False, "error": str(e)}
