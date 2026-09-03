import time
from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin


class TestAIProviderConfigRequest(BaseModel):
    provider_id:       Optional[int] = Field(default=None, description="ID del proveedor para reutilizar API key")
    provider:          str  = Field(min_length=1, max_length=50)
    endpoint_url:      str  = Field(min_length=1, max_length=500)
    model:             str  = Field(min_length=1, max_length=100)
    api_key:           str  = Field(default="", max_length=500)
    timeout_s:         int  = Field(default=120, ge=5, le=600)
    disable_reasoning: bool = Field(default=False)
    extra_headers:     dict = Field(default_factory=dict)
    extra_payload:     dict = Field(default_factory=dict)


class TestAIProviderConfigResponse(BaseModel):
    success: bool
    data:    Optional[dict] = None
    error:   Optional[str] = None


class TestAIProviderConfigPlugin(BasePlugin):
    """
    POST /api/ai/providers/test — Tests connection settings straight from the
    create/edit form, before the provider is saved. Lets you verify a provider
    (e.g. Gemini) works before committing it, instead of only being able to
    test rows that already exist.
    """

    def __init__(self, http, db, ai, logger):
        self.http = http
        self.db = db
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/ai/providers/test", "POST", self.execute,
            tags=["AI Config"],
            request_model=TestAIProviderConfigRequest,
            response_model=TestAIProviderConfigResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = TestAIProviderConfigRequest(**data)

            api_key = req.api_key
            if not api_key and req.provider_id:
                row = await self.db.query_one(
                    "SELECT api_key FROM ai_providers WHERE id = $1", [req.provider_id]
                )
                if row:
                    api_key = row["api_key"]

            config = {
                "provider":          req.provider,
                "endpoint_url":      req.endpoint_url,
                "model":             req.model,
                "api_key":           api_key,
                "timeout_s":         req.timeout_s,
                "disable_reasoning": req.disable_reasoning,
                "extra_headers":     req.extra_headers,
                "extra_payload":     req.extra_payload,
            }

            start = time.perf_counter()
            try:
                response = await self.ai.test_config(config)
                latency_ms = round((time.perf_counter() - start) * 1000)
                self.logger.info(f"[AIProviderConfigTest] OK — {latency_ms}ms — response: {response!r}")
                return {"success": True, "data": {"latency_ms": latency_ms, "response": response}}
            except Exception as e:
                latency_ms = round((time.perf_counter() - start) * 1000)
                self.logger.warning(f"[AIProviderConfigTest] failed — {e}")
                return {"success": False, "error": str(e), "data": {"latency_ms": latency_ms}}
        except Exception as e:
            self.logger.error(f"[AIProviderConfigTest] {e}")
            return {"success": False, "error": str(e)}
