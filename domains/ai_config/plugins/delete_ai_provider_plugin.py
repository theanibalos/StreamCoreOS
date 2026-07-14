from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class DeleteAIProviderResponse(BaseModel):
    success: bool
    error:   Optional[str] = None


class DeleteAIProviderPlugin(BasePlugin):
    """
    DELETE /api/ai/providers/{provider_id} — Removes a saved AI provider.

    If it was the active provider, active_provider_id is cleared and the
    live AITool is unloaded (app goes back to "unconfigured" instead of
    silently keeping the deleted provider's endpoint live).
    """

    def __init__(self, http, db, ai, logger):
        self.http = http
        self.db = db
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/ai/providers/{provider_id}", "DELETE", self.execute,
            tags=["AI Config"],
            response_model=DeleteAIProviderResponse,
        )

    async def execute(self, data: dict, context=None):
        raw_id = data.get("provider_id")
        if raw_id is None:
            return {"success": False, "error": "Missing provider_id"}
        try:
            provider_id = int(raw_id)

            cfg = await self.db.query_one("SELECT active_provider_id FROM ai_config WHERE id = 1")
            was_active = bool(cfg and cfg["active_provider_id"] == provider_id)

            if was_active:
                # Clear the FK reference first — ai_config.active_provider_id
                # REFERENCES ai_providers(id) and PRAGMA foreign_keys=ON, so
                # deleting the referenced row first would violate the constraint.
                await self.db.execute(
                    "UPDATE ai_config SET active_provider_id = NULL WHERE id = 1"
                )

            await self.db.execute("DELETE FROM ai_providers WHERE id = $1", [provider_id])

            if was_active:
                self.ai.load_config({})

            self.logger.info(f"[AIProviders] Deleted — id={provider_id}")
            return {"success": True}
        except Exception as e:
            self.logger.error(f"[DeleteAIProvider] {e}")
            return {"success": False, "error": str(e)}
