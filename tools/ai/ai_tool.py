import json
import re
import httpx
from microcoreos.base_tool import BaseTool


# ── Structured error ──────────────────────────────────────────────────────────

class AIError(Exception):
    """
    Structured error raised by AITool on every failure path.

    Use .code in plugins to decide how to respond without parsing strings:

        "not_configured"       — load_config() not called yet
        "auth_failed"          — 401, wrong API key
        "rate_limited"         — 429, slow down
        "model_not_found"      — 404, bad model name or endpoint path
        "context_too_long"     — 400, input exceeds model context window
        "invalid_request"      — 400, other provider validation error
        "provider_unavailable" — 5xx, server-side issue
        "empty_response"       — model replied with no content at all
        "invalid_response"     — unexpected response structure (no choices, etc.)
        "invalid_json"         — complete_json() could not parse model output
        "timeout"              — request exceeded timeout_s
        "connection_error"     — TCP connect to endpoint failed
        "provider_error"       — any other HTTP error
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        provider_detail: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.provider_detail = provider_detail

    def __repr__(self) -> str:
        parts = [f"AIError(code={self.code!r}"]
        if self.status_code:
            parts.append(f"status={self.status_code}")
        parts.append(f"msg={str(self)!r})")
        return " ".join(parts)


# ── Provider behaviour tables ─────────────────────────────────────────────────

# Extra payload fields injected when disable_reasoning=True, keyed by provider.
# Only providers that actually expose a reasoning-disable API are listed.
_REASONING_DISABLE_PAYLOAD: dict[str, dict] = {
    "openrouter": {"reasoning": {"exclude": True}},
    # anthropic native (non-compat) would need {"thinking": {"type": "disabled"}}
    # but we target the OpenAI-compat endpoint, so nothing extra is needed there.
}

# Extra HTTP headers injected by provider name.
_PROVIDER_EXTRA_HEADERS: dict[str, dict] = {
    "openrouter": {
        "HTTP-Referer": "https://streamcoreos",
        "X-Title": "StreamCoreOS",
    },
}

# Providers where response_format={"type":"json_object"} is reliably supported.
_JSON_MODE_PROVIDERS: frozenset[str] = frozenset({"openai", "groq", "openrouter", "anthropic_compat"})


# ── Tool ──────────────────────────────────────────────────────────────────────

class AITool(BaseTool):
    """
    Robust AI completions for local inference (Ollama, LM Studio, llama.cpp)
    and cloud providers via any OpenAI-compatible endpoint.

    Config is always pushed via load_config() — this tool never touches the DB.
    """

    @property
    def name(self) -> str:
        return "ai"

    async def setup(self):
        self._config: dict | None = None
        print("[AITool] Ready — waiting for config.")

    # ── Config management ─────────────────────────────────────────────────────

    def load_config(self, config: dict) -> None:
        """Push new config in, replacing it entirely. Called by RestoreAIConfigPlugin
        on boot and by the provider create/update/activate/delete plugins. Never
        triggers a DB read."""
        self._config = config

    def patch_config(self, fields: dict) -> None:
        """Merge fields into the current config without touching the rest — used
        when only chat-personality fields change and the provider connection
        settings (endpoint_url, api_key, ...) must be left untouched."""
        self._config = {**(self._config or {}), **fields}

    def is_configured(self) -> bool:
        return bool(
            self._config
            and self._config.get("endpoint_url")
            and self._config.get("model")
        )

    def get_config(self) -> dict | None:
        """Current config without api_key — safe to expose via HTTP."""
        if not self._config:
            return None
        return {
            "provider":           self._config.get("provider", "custom"),
            "endpoint_url":       self._config.get("endpoint_url", ""),
            "model":              self._config.get("model", ""),
            "has_api_key":        bool(self._config.get("api_key")),
            "timeout_s":          int(self._config.get("timeout_s") or 120),
            "disable_reasoning":  bool(self._config.get("disable_reasoning", False)),
            "extra_headers":      self._load_json_field(self._config, "extra_headers"),
            "extra_payload":      self._load_json_field(self._config, "extra_payload"),
            "chat_cooldown_s":    int(self._config.get("chat_cooldown_s") or 120),
            "chat_system_prompt": self._config.get("chat_system_prompt", ""),
            "chat_max_tokens":    int(self._config.get("chat_max_tokens") or 200),
            "chat_temperature":   float(self._config.get("chat_temperature") or 0.7),
            "updated_at":         self._config.get("updated_at"),
        }

    def get_chat_cooldown(self) -> int:
        return int((self._config or {}).get("chat_cooldown_s") or 120)

    def get_chat_personality(self) -> dict:
        cfg = self._config or {}
        return {
            "system_prompt": (
                cfg.get("chat_system_prompt")
                or "You are a helpful Twitch chat assistant. Be concise and reply in under 40 words."
            ),
            "max_tokens":  int(cfg.get("chat_max_tokens") or 200),
            "temperature": float(cfg.get("chat_temperature") or 0.7),
        }

    # ── Public API ────────────────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 300,
        temperature: float = 0.0,
    ) -> str:
        """
        Send messages to the configured LLM and return the text response.

        Raises AIError on all failure paths — check .code for cause.
        """
        if not self.is_configured():
            raise AIError(
                "not_configured",
                "AI tool is not configured. Set endpoint_url and model via /ai/config.",
            )
        return await self._call(self._config, messages, system, max_tokens, temperature, json_mode=False)

    async def complete_json(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 300,
        temperature: float = 0.0,
    ) -> dict:
        """
        Like complete(), but parses the response as a JSON object and returns it.

        The system prompt MUST instruct the model to reply with a JSON object.
        Handles markdown code fences (```json ... ```) automatically.
        Injects response_format=json_object for providers that support it
        (openai, groq, openrouter, anthropic_compat).

        Example system prompt:
            'Respond ONLY with: {"flagged": true|false, "reason": "..."}'

        Raises:
            AIError("invalid_json") — if the model response cannot be parsed.
            AIError(*)              — same codes as complete() for request errors.
        """
        if not self.is_configured():
            raise AIError(
                "not_configured",
                "AI tool is not configured. Set endpoint_url and model via /ai/config.",
            )
        text = await self._call(self._config, messages, system, max_tokens, temperature, json_mode=True)
        return self._parse_json_response(text)

    async def test_config(
        self,
        config: dict,
        max_tokens: int = 64,
    ) -> str:
        """
        Send a minimal probe request against an explicit config, without touching
        the live config. Used to test a saved-but-inactive provider — safe to call
        concurrently with complete()/complete_json() since it never mutates state.

        Raises AIError on all failure paths — check .code for cause.
        """
        if not config.get("endpoint_url") or not config.get("model"):
            raise AIError(
                "not_configured",
                "Provider config is missing endpoint_url or model.",
            )
        return await self._call(
            config,
            messages=[{"role": "user", "content": "Reply with just the word OK."}],
            system=None,
            max_tokens=max_tokens,
            temperature=0.0,
            json_mode=False,
        )

    # ── Core request ──────────────────────────────────────────────────────────

    async def _call(
        self,
        config: dict,
        messages: list[dict],
        system: str | None,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> str:
        provider  = (config.get("provider") or "custom").lower()
        timeout_s = float(config.get("timeout_s") or 120)
        endpoint  = config["endpoint_url"].rstrip("/")
        payload   = self._build_payload(config, provider, messages, system, max_tokens, temperature, json_mode)
        headers   = self._build_headers(config, provider)

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AIError(
                "timeout",
                f"Request timed out after {timeout_s}s ({type(exc).__name__})",
                provider_detail=str(exc),
            )
        except httpx.ConnectError as exc:
            raise AIError(
                "connection_error",
                f"Cannot connect to {endpoint}: {exc}",
                provider_detail=str(exc),
            )

        if not resp.is_success:
            raise self._classify_http_error(resp.status_code, resp.text)

        try:
            result = resp.json()
        except Exception:
            raise AIError(
                "invalid_response",
                f"Non-JSON response (HTTP {resp.status_code}): {resp.text[:400]!r}",
            )

        choice = self._unwrap_choices(result)
        return self._extract_content(choice)

    # ── Payload & headers builders ────────────────────────────────────────────

    def _build_payload(
        self,
        config: dict,
        provider: str,
        messages: list[dict],
        system: str | None,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> dict:
        all_messages: list[dict] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        payload: dict = {
            "model":       config["model"],
            "messages":    all_messages,
            "temperature": temperature,
            "max_tokens":  max_tokens,
            "stream":      False,
        }

        # JSON mode: inject response_format for capable providers only.
        # For local models (ollama, lm_studio, llama_cpp) we rely on prompt
        # engineering — adding an unsupported field causes a 400 on some servers.
        if json_mode and provider in _JSON_MODE_PROVIDERS:
            payload["response_format"] = {"type": "json_object"}

        # Reasoning disable: only inject for providers that expose the API.
        if config.get("disable_reasoning") and provider in _REASONING_DISABLE_PAYLOAD:
            payload.update(_REASONING_DISABLE_PAYLOAD[provider])

        # User-defined extra payload fields.
        # Common uses: {"num_ctx": 8192} for Ollama, {"num_predict": 256} for llama.cpp
        payload.update(self._load_json_field(config, "extra_payload"))

        return payload

    def _build_headers(self, config: dict, provider: str) -> dict:
        headers = {"Content-Type": "application/json"}

        api_key = (config.get("api_key") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Provider-specific default headers (e.g. OpenRouter attribution headers)
        for key, val in _PROVIDER_EXTRA_HEADERS.get(provider, {}).items():
            headers[key] = val

        # User-defined extra headers (e.g. proxy auth, org headers, custom auth schemes)
        headers.update(self._load_json_field(config, "extra_headers"))

        return headers

    # ── Response parsing ──────────────────────────────────────────────────────

    def _unwrap_choices(self, result: dict) -> dict:
        """Validate response structure and return the first choice dict."""
        # Some providers return HTTP 200 with an error key instead of choices
        if "error" in result and "choices" not in result:
            err = result["error"]
            msg = (err.get("message") or str(err)) if isinstance(err, dict) else str(err)
            raise AIError("provider_error", f"Provider returned an error body: {msg}")

        choices = result.get("choices")
        if not choices:
            raise AIError(
                "invalid_response",
                f"Response has no 'choices'. Keys: {list(result.keys())}. "
                f"Body (truncated): {json.dumps(result)[:400]}",
            )

        return choices[0]

    def _extract_content(self, choice: dict) -> str:
        """
        Extract text from a choice with ordered fallbacks:

        1. message.content           — standard OpenAI-compatible response
        2. message.reasoning_content — DeepSeek R1, Nemotron and similar models
                                       that put the full answer in the reasoning field
                                       when content is empty
        """
        msg = choice.get("message") or {}
        finish_reason = choice.get("finish_reason", "unknown")

        content = (msg.get("content") or "").strip()
        if content:
            return content

        reasoning = (msg.get("reasoning_content") or "").strip()
        if reasoning:
            return reasoning

        raise AIError(
            "empty_response",
            f"Model returned no content (finish_reason={finish_reason!r}, "
            f"message keys={list(msg.keys())})",
        )

    def _parse_json_response(self, text: str) -> dict:
        """
        Parse a JSON object from the model response.

        Handles:
        - Clean JSON: {"key": "val"}
        - Markdown fences: ```json\n{...}\n```
        - Preamble text before the JSON block
        """
        cleaned = text.strip()

        # Strip ```json ... ``` or ``` ... ``` fences
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if fence:
            cleaned = fence.group(1).strip()
        else:
            # Extract first {...} block in case model added preamble text
            obj_match = re.search(r"\{[\s\S]*\}", cleaned)
            if obj_match:
                cleaned = obj_match.group(0)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise AIError(
                "invalid_json",
                f"Model response is not valid JSON: {exc}. "
                f"Raw (truncated): {text[:400]!r}",
                provider_detail=text[:1000],
            )

        if not isinstance(parsed, dict):
            raise AIError(
                "invalid_json",
                f"Expected a JSON object, got {type(parsed).__name__}. "
                f"Raw: {text[:400]!r}",
            )

        return parsed

    # ── HTTP error classification ─────────────────────────────────────────────

    def _classify_http_error(self, status_code: int, body: str) -> AIError:
        """Map HTTP status + provider body into a typed AIError."""
        # Try to extract the human-readable message from the JSON error body
        provider_detail = body[:1000]
        try:
            data = json.loads(body)
            err_obj = data.get("error") or data
            if isinstance(err_obj, dict):
                provider_detail = (
                    err_obj.get("message") or err_obj.get("msg") or provider_detail
                )
        except Exception:
            pass

        kw = dict(status_code=status_code, provider_detail=provider_detail)

        if status_code == 401:
            return AIError("auth_failed",
                f"Authentication failed — check your API key. Provider: {provider_detail}", **kw)
        if status_code == 429:
            return AIError("rate_limited",
                f"Rate limit exceeded — reduce request frequency. Provider: {provider_detail}", **kw)
        if status_code == 404:
            return AIError("model_not_found",
                f"Endpoint or model not found — check endpoint_url and model. Provider: {provider_detail}", **kw)
        if status_code == 400:
            lower = provider_detail.lower()
            if any(kw_str in lower for kw_str in ("context", "token", "length", "too long", "maximum", "exceed")):
                return AIError("context_too_long",
                    f"Input exceeds model context window. Provider: {provider_detail}", **kw)
            return AIError("invalid_request",
                f"Invalid request (400). Provider: {provider_detail}", **kw)
        if status_code in (500, 502, 503, 504):
            return AIError("provider_unavailable",
                f"Provider unavailable (HTTP {status_code}). Provider: {provider_detail}", **kw)

        return AIError("provider_error",
            f"HTTP {status_code}. Provider: {provider_detail}", **kw)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_json_field(self, config: dict, key: str) -> dict:
        """Read a config field that may be stored as a dict or a JSON string."""
        val = (config or {}).get(key) or {}
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except Exception:
                return {}
        return val if isinstance(val, dict) else {}

    # ── Interface description (consumed by context_manager) ───────────────────

    def get_interface_description(self) -> str:
        return """
AI Tool (ai):
    - PURPOSE: Robust AI completions for local (Ollama, LM Studio, llama.cpp) and cloud
      providers via any OpenAI-compatible endpoint.
      Config is pushed via load_config() — never touches DB directly.
    - PROVIDERS: ollama | lm_studio | llama_cpp | openai | openrouter | groq | anthropic_compat | custom
    - CONFIG FIELDS (set via PUT /ai/config):
        provider           — provider name (controls header/payload behaviour)
        endpoint_url       — full completions URL
        api_key            — Bearer token (empty for local providers)
        model              — model name as the provider expects it
        timeout_s          — request timeout in seconds (default: 120)
        disable_reasoning  — suppress reasoning tokens when provider supports it
        extra_headers      — JSON dict of additional HTTP headers
        extra_payload      — JSON dict of extra payload fields
                             e.g. {"num_ctx": 8192} for Ollama context size
                             e.g. {"num_predict": 256} for llama.cpp token limit
        chat_cooldown_s    — !ia command per-user cooldown in seconds
        chat_system_prompt — personality for !ia command
        chat_max_tokens    — max tokens for !ia responses
        chat_temperature   — temperature for !ia responses
    - ERRORS: All methods raise AIError. Check .code for machine-readable cause:
        "not_configured"       load_config() not called
        "auth_failed"          bad API key (401)
        "rate_limited"         rate limit hit (429)
        "model_not_found"      bad model/endpoint (404)
        "context_too_long"     input exceeds context (400)
        "invalid_request"      other bad request (400)
        "provider_unavailable" server error (5xx)
        "empty_response"       model returned no content
        "invalid_response"     unexpected response structure
        "invalid_json"         complete_json() couldn't parse response
        "timeout"              request exceeded timeout_s
        "connection_error"     could not connect to endpoint
        "provider_error"       any other HTTP error
    - CAPABILITIES:
        - await complete(messages, system?, max_tokens?, temperature?) -> str
            Returns the model's text response.
        - await complete_json(messages, system?, max_tokens?, temperature?) -> dict
            Returns a parsed JSON object. System prompt must instruct the model to
            respond with JSON. Strips markdown fences automatically.
            Injects response_format=json_object for capable providers
            (openai, groq, openrouter, anthropic_compat).
            Example system: 'Respond ONLY with: {"flagged": true|false, "reason": "..."}'
        - is_configured() -> bool
        - get_config() -> dict | None  (never exposes api_key)
        - load_config(config: dict)
        - patch_config(fields: dict)
        - await test_config(config: dict, max_tokens?) -> str
        - get_chat_cooldown() -> int
        - get_chat_personality() -> dict
    - LOCAL ENDPOINTS:
        Ollama:    http://localhost:11434/v1/chat/completions
        LM Studio: http://localhost:1234/v1/chat/completions
        llama.cpp: http://localhost:8080/v1/chat/completions
    - CLOUD ENDPOINTS:
        OpenAI:     https://api.openai.com/v1/chat/completions
        Groq:       https://api.groq.com/openai/v1/chat/completions
        OpenRouter: https://openrouter.ai/api/v1/chat/completions
    """

    async def shutdown(self):
        pass
