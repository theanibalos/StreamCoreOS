import json
from typing import Optional, List
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class AIProviderEntry(BaseModel):
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


class ListAIProvidersResponse(BaseModel):
    success: bool
    data:    Optional[List[AIProviderEntry]] = None
    error:   Optional[str] = None


class ListAIProvidersPlugin(BasePlugin):
    """GET /api/ai/providers — lists all saved AI providers (api_key never exposed)."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/ai/providers", "GET", self.execute,
            tags=["AI Config"],
            response_model=ListAIProvidersResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rows = await self.db.query("SELECT * FROM ai_providers ORDER BY created_at DESC")
            cfg = await self.db.query_one("SELECT active_provider_id FROM ai_config WHERE id = 1")
            active_id = cfg["active_provider_id"] if cfg else None

            entries = []
            for r in rows:
                entries.append({
                    "id":                r["id"],
                    "name":              r["name"],
                    "provider":          r["provider"],
                    "endpoint_url":      r["endpoint_url"],
                    "model":             r["model"],
                    "has_api_key":       bool(r["api_key"]),
                    "timeout_s":         r["timeout_s"],
                    "disable_reasoning": bool(r["disable_reasoning"]),
                    "extra_headers":     self._load_json(r.get("extra_headers")),
                    "extra_payload":     self._load_json(r.get("extra_payload")),
                    "is_active":         r["id"] == active_id,
                    "updated_at":        r["updated_at"],
                })
            return {"success": True, "data": entries}
        except Exception as e:
            self.logger.error(f"[ListAIProviders] {e}")
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
