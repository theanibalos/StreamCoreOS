from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin

_NS = "ia_config"
_KEY = "chat_ia_enabled"


class SetIAEnabledRequest(BaseModel):
    enabled: bool = Field(description="Estado de activación del comando !ia")


class IAEnabledData(BaseModel):
    enabled: bool


class IAEnabledResponse(BaseModel):
    success: bool
    data: Optional[IAEnabledData] = None
    error: Optional[str] = None


class ToggleIAChatPlugin(BasePlugin):
    """
    GET  /ai/ia/enabled  — returns whether !ia is active.
    PUT  /ai/ia/enabled  — toggles it (persists to DB, updates state immediately).
    on_boot              — loads value from DB into state so ia_chat_plugin can read it.
    """

    def __init__(self, http, db, state, logger):
        self.http = http
        self.db = db
        self.state = state
        self.logger = logger

    async def on_boot(self):
        await self._load_from_db()

        self.http.add_endpoint(
            "/api/ai/ia/enabled", "GET", self._get,
            tags=["AI Config"],
            response_model=IAEnabledResponse,
        )
        self.http.add_endpoint(
            "/api/ai/ia/enabled", "PUT", self._set,
            tags=["AI Config"],
            request_model=SetIAEnabledRequest,
            response_model=IAEnabledResponse,
        )

    async def _load_from_db(self):
        try:
            row = await self.db.query_one(
                "SELECT chat_ia_enabled FROM ai_config WHERE id = 1"
            )
            enabled = bool(row["chat_ia_enabled"]) if row else True
            await self.state.set(_KEY, enabled, namespace=_NS)
        except Exception:
            await self.state.set(_KEY, True, namespace=_NS)

    async def _get(self, data: dict, context=None):
        enabled = await self.state.get(_KEY, default=True, namespace=_NS)
        return {"success": True, "data": {"enabled": enabled}}

    async def _set(self, data: dict, context=None):
        try:
            req = SetIAEnabledRequest(**data)
            await self.db.execute(
                "UPDATE ai_config SET chat_ia_enabled=$1 WHERE id=1",
                [int(req.enabled)],
            )
            await self.state.set(_KEY, req.enabled, namespace=_NS)
            self.logger.info(f"[ToggleIAChat] chat_ia_enabled={req.enabled}")
            return {"success": True, "data": {"enabled": req.enabled}}
        except Exception as e:
            self.logger.error(f"[ToggleIAChat] {e}")
            return {"success": False, "error": str(e)}
