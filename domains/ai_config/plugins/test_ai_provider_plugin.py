import json
import time
from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class TestAIProviderResponse(BaseModel):
    success: bool
    data:    Optional[dict] = None
    error:   Optional[str] = None


class TestAIProviderPlugin(BasePlugin):
    """
    POST /api/ai/providers/{provider_id}/test — Tests a saved provider by id,
    independent of which provider is currently active. This is what lets you
    verify a provider (e.g. Gemini) without first making it live, and without
    a different provider's config racing in and getting tested instead.
    """

    def __init__(self, http, db, ai, logger):
        self.http = http
        self.db = db
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/ai/providers/{provider_id}/test", "POST", self.execute,
            tags=["AI Config"],
            response_model=TestAIProviderResponse,
        )

    async def execute(self, data: dict, context=None):
        raw_id = data.get("provider_id")
        if raw_id is None:
            return {"success": False, "error": "Missing provider_id"}

        provider_id = int(raw_id)
        row = await self.db.query_one("SELECT * FROM ai_providers WHERE id = $1", [provider_id])
        if not row:
            return {"success": False, "error": "Provider not found."}

        config = {
            "provider":          row["provider"],
            "endpoint_url":      row["endpoint_url"],
            "model":             row["model"],
            "api_key":           row["api_key"],
            "timeout_s":         row["timeout_s"],
            "disable_reasoning": bool(row["disable_reasoning"]),
            "extra_headers":     self._load_json(row.get("extra_headers")),
            "extra_payload":     self._load_json(row.get("extra_payload")),
        }

        start = time.perf_counter()
        try:
            response = await self.ai.test_config(config)
            latency_ms = round((time.perf_counter() - start) * 1000)
            self.logger.info(f"[AIProviderTest] id={provider_id} OK — {latency_ms}ms — response: {response!r}")
            return {"success": True, "data": {"latency_ms": latency_ms, "response": response}}
        except Exception as e:
            latency_ms = round((time.perf_counter() - start) * 1000)
            self.logger.warning(f"[AIProviderTest] id={provider_id} failed — {e}")
            return {"success": False, "error": str(e), "data": {"latency_ms": latency_ms}}

    def _load_json(self, val) -> dict:
        if not val:
            return {}
        if isinstance(val, dict):
            return val
        try:
            return json.loads(val)
        except Exception:
            return {}
