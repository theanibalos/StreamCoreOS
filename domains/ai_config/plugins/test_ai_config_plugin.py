import time
from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class TestAIConfigResponse(BaseModel):
    success: bool
    data:    Optional[dict] = None
    error:   Optional[str] = None


class TestAIConfigPlugin(BasePlugin):
    """
    POST /ai/test — Sends a minimal prompt to the configured AI and measures latency.
    Used by the frontend to verify the connection after saving config.
    """

    def __init__(self, http, ai, logger):
        self.http = http
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/ai/test", "POST", self.execute,
            tags=["AI Config"],
            response_model=TestAIConfigResponse,
        )

    async def execute(self, data: dict, context=None):
        if not self.ai.is_configured():
            return {"success": False, "error": "AI is not configured."}

        start = time.perf_counter()
        try:
            response = await self.ai.complete(
                messages=[{"role": "user", "content": "Reply with just the word OK."}],
                max_tokens=5,
                temperature=0.0,
            )
            latency_ms = round((time.perf_counter() - start) * 1000)
            self.logger.info(f"[AITest] Connection OK — {latency_ms}ms — response: {response!r}")
            return {"success": True, "data": {"latency_ms": latency_ms, "response": response}}
        except Exception as e:
            latency_ms = round((time.perf_counter() - start) * 1000)
            self.logger.warning(f"[AITest] Connection failed — {e}")
            return {"success": False, "error": str(e), "data": {"latency_ms": latency_ms}}
