# 📜 SYSTEM MANIFEST

> This file is ALL you need to build a plugin. For advanced topics (testing, observability, creating tools), see [INSTRUCTIONS_FOR_AI.md](INSTRUCTIONS_FOR_AI.md).

## ⚡ Plugin Quick Start

**Location**: `domains/{domain}/plugins/{feature}_plugin.py` — 1 file = 1 feature.

### Template

```python
from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin

# Request/Response schemas live HERE, not in models/
class CreateThingRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)

class ThingData(BaseModel):
    id: int
    name: str

class CreateThingResponse(BaseModel):
    success: bool
    data: Optional[ThingData] = None
    error: Optional[str] = None

class CreateThingPlugin(BasePlugin):
    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/things", "POST", self.execute,
            tags=["Things"],
            request_model=CreateThingRequest,
            response_model=CreateThingResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = CreateThingRequest(**data)
            thing_id = await self.db.execute(
                "INSERT INTO things (name) VALUES ($1) RETURNING id", [req.name]
            )
            await self.bus.publish("thing.created", {"id": thing_id})
            return {"success": True, "data": {"id": thing_id, "name": req.name}}
        except Exception as e:
            # Technical error logged server-side, safe message for client
            self.logger.error(f"Failed to create thing: {e}")
            return {"success": False, "error": "Database error"}
```

### New Domain Structure

```
domains/{name}/
  __init__.py
  models/{name}.py        <- Entity: DB mirror only (Pydantic BaseModel)
  migrations/001_xxx.sql  <- Raw SQL, auto-executed on boot
  plugins/                <- 1 file = 1 feature
```

### Critical Rules

1. **Never modify `main.py`** — Kernel auto-discovers everything.
2. **DI by name** — `__init__` param names must match tool `name` properties.
3. **Schemas inline** — Request AND response schemas go in the plugin file, not in `models/`.
4. **No cross-domain imports** — Use `event_bus` for inter-domain communication.
5. **Return format** — Always `{"success": bool, "data": ..., "error": ...}`.
6. **Use `Field`** — Never bare `str`/`int` in request schemas. Use `Field(min_length=1)` etc.
7. **SQL placeholders** — Always `$1, $2, $3...` (never `?`).
8. **Always pass `response_model=`** to `add_endpoint` — generates OpenAPI docs.
9. **Never expose sensitive fields** — Define response schema with only safe fields.
10. **No hardcoded imports** — Never `from tools.x import X`. Use DI.

---

## 🛠️ Quick Architecture Ref
- **Pattern**: `__init__` (DI) -> `on_boot` (Register) -> handler methods (Action).
- **Injection**: Tools are injected by name in the constructor.

## 🛠️ Available Tools
Check method signatures before implementation.

### 🔧 Tool: `ai` (Status: ✅)
```text
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
```

### 🔧 Tool: `config` (Status: ✅)
```text
Configuration Tool (config):
        - PURPOSE: Validated access to environment variables for plugins.
          Tools read their own env vars with os.getenv() — this tool is for plugins.
        - CAPABILITIES:
            - get(key, default=None, required=False) -> str | None:
                Returns the value of the environment variable.
                If required=True and the variable is not set, raises EnvironmentError.
            - require(*keys) -> None:
                Validates that all specified variables are set.
                Call in on_boot() to fail early with a clear error message.
                Example: self.config.require("STRIPE_KEY", "SENDGRID_KEY")
```

### 🔧 Tool: `event_bus` (Status: ✅)
```text
Async Event Bus Tool (event_bus):
        - PURPOSE: Non-blocking communication between plugins. Pub/Sub and Async RPC.
        - SUBSCRIBER SIGNATURE: async def handler(self, data: dict)
        - CAPABILITIES:
            - await publish(event_name, data): Fire-and-forget broadcast.
            - await subscribe(event_name, callback): Register a subscriber.
                Use event_name='*' for wildcard (observability only, no RPC).
            - await unsubscribe(event_name, callback): Remove a subscriber.
            - await request(event_name, data, timeout=5): Async RPC.
                The subscriber must return a non-None dict.
            - get_trace_history() -> list: Last 500 event records with causality data.
            - get_subscribers() -> dict: Current subscriber map {event_name: [subscriber_names]}.
            - add_listener(callback): Sink pattern — called with full trace record on every event.
                Signature: callback(record: dict) — record has: id, event, emitter, subscribers, payload_keys, timestamp.
                Use for real-time observability (e.g. WebSocket broadcast). Non-blocking.
            - add_failure_listener(callback): Sink called when a subscriber raises during dispatch.
                Signature: callback(record: dict) — record has: event, event_id, subscriber, error.
                Use to implement dead-letter alerting. Non-blocking — keep it fast.
        - WELL-KNOWN EVENTS:
            - "overlay.vars.set" — publish a flat dict of variables to push them to all
              live overlays (OBS browser sources). Persisted in the overlay_vars table,
              broadcast instantly via SSE, readable in overlay JS as data.stats[key].
              Example: await self.bus.publish("overlay.vars.set", {"juego.actual": "Elden Ring"})
```

### 🔧 Tool: `http` (Status: ✅)
```text
HTTP Server Tool (http):
        - PURPOSE: FastAPI-powered HTTP gateway. Supports REST, static files, WebSockets and SSE.
        - HANDLER SIGNATURE: async def execute(self, data: dict, context: HttpContext) -> dict
          'data' = flat merge of [path params] + [query params] + [body/form fields].
          Special keys in 'data':
            - data["_auth"]: contains the payload from auth_validator if successful.
            - data["_files"]: list of FastAPI UploadFile objects (only if has_files=True).
        - CAPABILITIES:
            - add_endpoint(path, method, handler, tags=None, request_model=None,
                           response_model=None, auth_validator=None, has_files=False):
                - has_files: if True, enables multipart/form-data. Request model fields 
                  become Form fields. To use a file: file = data["_files"][0]; 
                  await s3.upload_fileobj(file.filename, file.file, content_type=file.content_type)
            - mount_static(path, directory_path): Serve static files from a directory.
                  ALWAYS use /api/ prefix (e.g. "/api/uploads") — the frontend proxy only
                  forwards /api/* to the backend. Without it, requests will 404 in dev.
            - add_ws_endpoint(path, on_connect, on_disconnect=None): WebSocket support.
            - add_sse_endpoint(path, generator, tags=None, auth_validator=None): 
                Server-Sent Events. generator yields formatted strings: "data: {...}\n\n".
        - HttpContext CAPABILITIES (inside handler):
            - context.set_status(code: int): Override HTTP status (default: 200).
            - context.redirect(url: str, status=302): Redirect to another URL.
            - context.set_cookie(key, value, max_age=3600, httponly=True, samesite='lax'): Set cookie.
            - context.set_header(key, value): Add custom response header.
            - context.set_binary_response(content: bytes, media_type: str): Return raw file.
        - RESPONSE CONTRACT:
            - Standard: return {"success": bool, "data": ..., "error": ...}
            - WARNING: All values in 'data' must be JSON-serializable. Pydantic model 
              instances are NOT serializable — always call .model_dump() before returning.
```

### 🔧 Tool: `telemetry` (Status: ✅)
```text
Telemetry Tool (telemetry):
        - PURPOSE: OpenTelemetry distributed tracing. Auto-instruments all tool calls via ToolProxy.
          No changes needed in plugins or existing tools to get basic spans.
        - ACTIVATION: Set OTEL_ENABLED=true. Degrades gracefully if disabled or packages missing.
        - ENV VARS:
            - OTEL_ENABLED: "true" to activate (default: "false").
            - OTEL_SERVICE_NAME: Service name in traces (default: "microcoreos").
            - OTEL_EXPORTER_OTLP_ENDPOINT: OTLP/gRPC endpoint (e.g. "http://jaeger:4317").
              If not set, traces are printed to console (development mode).
        - CAPABILITIES:
            - get_tracer(scope: str) -> Tracer: Named tracer for custom spans inside a plugin.
                Usage: tracer = self.telemetry.get_tracer("my_plugin")
                       with tracer.start_as_current_span("my_operation"): ...
                Returns a no-op tracer if OTel is disabled — safe to use unconditionally.
        - AUTO-INSTRUMENTATION (zero config):
            Every tool call (db.execute, event_bus.publish, auth.create_token, etc.)
            gets a span automatically via ToolProxy. No plugin changes needed.
        - DRIVER-LEVEL INSTRUMENTATION (optional, per tool):
            Tools can implement on_instrument(tracer_provider) in BaseTool to add
            framework-specific spans (SQL query text, HTTP route, etc.).
        - INSTALL:
            uv add opentelemetry-sdk opentelemetry-exporter-otlp
```

### 🔧 Tool: `twitch` (Status: ✅)
```text
Twitch Tool (twitch):
        - PURPOSE: Complete Twitch platform wrapper — OAuth, Helix API, EventSub WebSocket, IRC Chat.
        - ENV VARS: TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, TWITCH_REDIRECT_URI (optional).
        - PATTERN: Register in on_boot() → user authenticates → call connect() → receive events.

        REGISTRATION (call in on_boot, before connect):
          - register(event_type, version, scopes, condition?):
              Declare an EventSub subscription and its required OAuth scopes.
              condition defaults to {"broadcaster_user_id": "{broadcaster_id}"}.
              {broadcaster_id} is replaced automatically when connect() is called.
              Example: twitch.register("channel.follow", "2", ["moderator:read:followers"])
          - on_event(event_type, callback):
              Register a callback for a Twitch event. Use '*' for all events.
              Signature: async def handler(event_data: dict)

        CHAT (via EventSub — not IRC):
          - To receive chat messages: register("channel.chat.message", "1",
              scopes=["user:read:chat"],
              condition={"broadcaster_user_id": "{broadcaster_id}", "user_id": "{broadcaster_id}"})
            then on_event("channel.chat.message", callback)
          - To send chat messages: await send_message(channel, message)
              Requires user:write:chat scope (add via require_scopes).

        OAUTH:
          - get_auth_url() -> tuple[str, str]:
              Returns (url, state). Save state for CSRF validation in the callback.
          - await exchange_code(code) -> dict:
              Exchange OAuth code for tokens: {access_token, refresh_token, scope, expires_in}
          - await refresh_user_token(refresh_token) -> dict:
              Refresh a user token. Returns new {access_token, refresh_token, ...}
          - await get_user_info(access_token) -> dict:
              Get the authenticated user's Twitch profile {id, login, display_name, ...}

        CONNECTION:
          - await connect(access_token, refresh_token, broadcaster_id, twitch_login):
              Connect EventSub WebSocket + IRC chat. Creates all registered subscriptions.
          - await update_access_token(new_token, new_refresh_token?):
              Update the active session tokens in memory without disconnecting EventSub.
          - await disconnect(): Disconnect everything.

        CHAT:
          - await send_message(channel, message): Send a chat message.

        HELIX API:
          - await get(endpoint, params?, user_token?): GET to Helix.
          - await post(endpoint, body?, user_token?): POST to Helix.
          - await delete(endpoint, params?, user_token?): DELETE to Helix.
```

### 🔧 Tool: `http_client` (Status: ✅)
```text
HTTP Client Tool (http_client):
        - PURPOSE: Make outgoing HTTP requests from plugins. Async, backed by httpx.
        - RESPONSE: All methods return a dict:
            {
              "status": int,       # HTTP status code (200, 404, ...)
              "ok": bool,          # True if status < 400
              "json": dict | None, # Parsed JSON body (None if not JSON)
              "text": str,         # Raw response body
              "headers": dict      # Response headers
            }
        - ERRORS:
            TimeoutError      — request exceeded the timeout
            ConnectionError   — could not reach the server
        - CAPABILITIES:
            - await get(url, params?, headers?, timeout=10) -> dict
            - await post(url, json?, data?, headers?, timeout=10) -> dict
            - await put(url, json?, data?, headers?, timeout=10) -> dict
            - await delete(url, params?, headers?, timeout=10) -> dict
        - USAGE IN PLUGIN __init__: def __init__(self, http_client, ...)
```

### 🔧 Tool: `auth` (Status: ✅)
```text
Authentication Tool (auth):
        - PURPOSE: Manage system security, password hashing, and JWT token lifecycle.
        - CAPABILITIES:
            - hash_password(password: str) -> str: Securely hashes a plain-text password using bcrypt.
            - verify_password(password: str, hashed_password: str) -> bool: Verifies if a password matches its hash.
            - create_token(data: dict, expires_delta: Optional[int] = None) -> str: 
                Generates a JWT signed token. 'data' should contain claims (e.g. {'sub': user_id}). 
                'expires_delta' is optional minutes until expiration.
            - decode_token(token: str) -> dict: 
                Verifies and decodes a JWT token. Returns the payload dictionary. 
                Raises Exception if token is expired or invalid.
            - validate_token(token: str) -> dict | None:
                Safe, non-throwing token validation. Returns the decoded payload
                if valid, or None if expired/invalid. Ideal for middleware guards.
```

### 🔧 Tool: `context_manager` (Status: ✅)
```text
Context Manager Tool (context_manager):
        - PURPOSE: Automatically manages and generates live AI contextual documentation.
        - CAPABILITIES:
            - Reads the system registry.
            - Exports active tools, health status, and domain models to AI_CONTEXT.md.
            - Regenerates AI_CONTEXT.md on every boot — always up to date with the live system.
```

### 🔧 Tool: `logger` (Status: ✅)
```text
Logging Tool (logger):
        - PURPOSE: Record system events and business activity for audit and debugging.
        - CAPABILITIES:
            - info(message): General information.
            - error(message): Critical failures.
            - warning(message): Non-critical alerts.
            - add_sink(callback): Connect external observability (e.g. to EventBus).
                Sink signature: callback(level: str, message: str, timestamp: str, identity: str)
                'identity' is the current plugin/tool context (from current_identity_var).
                Use it to attribute errors to specific plugins for health tracking.
```

### 🔧 Tool: `state` (Status: ✅)
```text
In-Memory State Tool (state):
        - PURPOSE: Share volatile global data between plugins safely.
        - IDEAL FOR: Counters, temporary caches, and shared business semaphores.
        - CAPABILITIES:
            - set(key, value, namespace='default'): Store a value.
            - get(key, default=None, namespace='default'): Retrieve a value (None if missing).
            - has(key, namespace='default'): Returns True if key exists.
            - keys(namespace='default'): Returns list of all keys in the namespace.
            - get_all(namespace='default'): Returns a shallow copy of all key-value pairs.
            - increment(key, amount=1, namespace='default'): Atomic increment. Starts at 0.
            - delete(key, namespace='default'): Delete a key (no-op if missing).
            - clear(namespace='default'): Remove all keys in the namespace.
```

### 🔧 Tool: `registry` (Status: ✅)
```text
Systems Registry Tool (registry):
        - PURPOSE: Introspection and discovery of the system's architecture at runtime.
        - CAPABILITIES:
            - get_system_dump() -> dict: Full inventory of active Tools, Domains and Plugins.
                Returns:
                {
                  "tools": {
                    "<tool_name>": {"status": "OK"|"FAIL"|"DEAD", "message": str|None}
                  },
                  "plugins": {
                    "<PluginClassName>": {
                      "status": "BOOTING"|"RUNNING"|"READY"|"DEAD",
                      "error": str|None,
                      "domain": str,
                      "class": str,
                      "dependencies": ["tool_name", ...]  # tools injected in __init__
                    }
                  },
                  "domains": { ... }
                }
                NOTE: status is updated REACTIVELY (on exception via ToolProxy).
                A tool that silently stopped responding may still show "OK".
            - get_domain_metadata() -> dict: Detailed analysis of models and schemas.
            - get_metrics() -> list[dict]: Last 1000 tool call records.
                Each record: {tool, method, duration_ms, success, timestamp}.
                Use to build /system/metrics or feed into an observability sink.
            - add_metrics_sink(callback): Register a sink for real-time metric records.
                Signature: callback(record: dict).
                Called synchronously on every tool method call — keep it fast.
            - update_tool_status(name, status, message=None): Manually override a tool's health status.
                status: "OK" | "FAIL" | "DEAD".
                Intended for health-check plugins that verify tools proactively.
```

### 🔧 Tool: `scheduler` (Status: ✅)
```text
Scheduler Tool (scheduler):
        - PURPOSE: Background job scheduling — cron-style recurring jobs and one-shot timed jobs.
          Backed by APScheduler AsyncIOScheduler. Zero infrastructure required.
          Supports both async and sync callbacks transparently.
        - CAPABILITIES:
            - add_job(cron_expr: str, callback, job_id?: str) -> str:
                Schedule a recurring job with a 5-field cron expression.
                e.g. "*/5 * * * *" = every 5 min, "0 9 * * 1-5" = weekdays at 09:00.
                Returns job_id (auto-generated if not provided).
                Providing a stable job_id prevents duplicates on restart.
            - add_one_shot(run_at: datetime, callback, job_id?: str) -> str:
                Schedule a one-time job at a specific datetime (timezone-aware).
                Returns job_id.
            - remove_job(job_id: str) -> bool:
                Remove a job by ID. Returns True if removed, False if not found.
            - list_jobs() -> list[dict]:
                Snapshot of all scheduled jobs: [{id, next_run, trigger}].
        - REGISTER IN on_boot(): jobs are collected during on_boot(), scheduler starts
          in on_boot_complete() after all plugins have registered.
        - SWAP: replace with Celery beat by creating a new tool with name = "scheduler"
          and the same 4-method API. Plugins do not change.
```

### 🔧 Tool: `db` (Status: ✅)
```text
Async SQLite Persistence Tool (sqlite):
        - PURPOSE: Drop-in replacement for PostgreSQL. Lightweight relational data
          storage using SQLite with async access. Accepts PostgreSQL-style placeholders
          ($1, $2...) and converts them transparently to SQLite's native '?'.
        - PLACEHOLDERS: Use $1, $2, $3... (SAME as PostgreSQL — swap-compatible).
        - CAPABILITIES:
            - await query(sql, params?) → list[dict]: Read multiple rows (SELECT).
            - await query_one(sql, params?) → dict | None: Read a single row (SELECT).
            - await execute(sql, params?) → int | None: Write data (INSERT/UPDATE/DELETE).
              With RETURNING (SQLite 3.35+): returns the first column value.
              INSERT without RETURNING: returns lastrowid. Others: returns affected row count.
            - await execute_many(sql, params_list) → None: Batch writes.
            - async with transaction() as tx: Explicit transaction block with auto-commit/rollback.
              Inside tx: tx.query(), tx.query_one(), tx.execute() — same signatures.
            - await health_check() → bool: Verify database connectivity.
        - EXCEPTIONS: Raises DatabaseError or DatabaseConnectionError on failure.
```

### 🔧 Tool: `tts` (Status: ✅)
```text
TTS Tool (tts):
    - PURPOSE: Universal TTS router with swappable providers. Plugins never
      interact with providers directly — just call generate(text, voice_id).
    - VOICE ID FORMAT: "<provider>:<raw_id>"
        edge_tts:es-ES-AlvaroNeural
        voicebox:b7e63948-323c-4711-be5a-1a44ef1f2be6
    - FALLBACK: If the requested provider is unavailable, falls back silently
      to the edge_tts default voice. edge_tts is always available.
    - PROVIDER CONFIG: env vars only (VOICEBOX_HOST, VOICEBOX_PORT, VOICEBOX_TIMEOUT_S,
      EDGE_TTS_DEFAULT_VOICE). Never stored in DB.
    - BEHAVIORAL CONFIG: pushed via load_config() from DB on boot and on PUT /tts/settings.
    - API:
        await generate(text, voice_id?) → bytes (MP3 or WAV)
        await list_voices()             → list[{id, name, gender, locale, provider}]
        load_config(config: dict)       → sets behavioral settings
        get_config()                    → dict (includes providers availability)
        is_available()                  → bool
        get_default_voice()             → namespaced voice id
        get_provider()                  → provider name of default voice
        get_providers()                 → dict[provider_name, is_available]
```

## 📦 Domains

### `ai_config`
- **Table `ai_config`**: provider (str), endpoint_url (str), model (str), updated_at (str)
- **Endpoints**: GET /api/ai/config, GET /api/ai/ia/enabled, POST /api/ai/test, PUT /api/ai/config, PUT /api/ai/ia/enabled
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: ai, db, http, logger, state
- **Plugins**: GetAIConfigPlugin, RestoreAIConfigPlugin, SaveAIConfigPlugin, TestAIConfigPlugin, ToggleIAChatPlugin

### `chat_bot`
- **Table `chat_command`**: name (str), response (str), cooldown_s (int), enabled (int), created_at (str), channel (str), user_id (str), display_name (str), message (str), is_command (int), timestamp (str)
- **Table `chat_var`**: name (str), value (str), enabled (int), created_at (str)
- **Endpoints**: DELETE /api/chat/commands/{id}, DELETE /api/chat/vars/{id}, GET /api/chat/badges, GET /api/chat/commands, GET /api/chat/reminders, GET /api/chat/vars, POST /api/chat/commands, POST /api/chat/vars, PUT /api/chat/commands/{id}, PUT /api/chat/vars/{id}, SSE /api/chat/stream
- **Events emitted**: `chat.command.executed` (channel, command, display_name, user_id), `chat.command.received` (args, command), `chat.message.received` ()
- **Events consumed**: none
- **Dependencies**: ai, db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: ChatAutoResponsePlugin, ChatBadgesPlugin, ChatCommandHandlerPlugin, ChatMessageDispatcherPlugin, ChatStreamPlugin, CommandsListPlugin, CreateCommandPlugin, CreateVarPlugin, DeleteCommandPlugin, DeleteVarPlugin, EchoReminderPlugin, IAChatPlugin, ListCommandsPlugin, ListRemindersPlugin, ListVarsPlugin, UpdateCommandPlugin, UpdateVarPlugin, VarCommandPlugin

### `dashboard`
- **Table `channel_stats`**: recorded_at (str), viewer_count (int), follower_count (int)
- **Endpoints**: GET /api/dashboard/stats, GET /api/dashboard/stats/history, POST /api/dashboard/alerts/test, SSE /api/dashboard/alerts
- **Events emitted**: `dashboard.stats.updated` (follower_count, viewer_count)
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: ChannelStatsCollectorPlugin, ChannelStatsHistoryPlugin, DashboardAlertsPlugin, DashboardStatsPlugin

### `moderation`
- **Table `mod_rule`**: type (str), value (Optional[str]), action (str), duration_s (Optional[int]), enabled (int), twitch_id (str), display_name (str), reason (str), rule_id (Optional[int]), created_at (str)
- **Endpoints**: DELETE /api/moderation/rules/{id}, GET /api/moderation/log, GET /api/moderation/rules, POST /api/moderation/ban, POST /api/moderation/rules, POST /api/moderation/timeout, POST /api/moderation/unban, PUT /api/moderation/rules/{id}
- **Events emitted**: `moderation.action.taken` (action, display_name, reason, rule_id, twitch_id), `moderation.rules.updated` (rule_id)
- **Events consumed**: none
- **Dependencies**: ai, db, event_bus, http, logger, state, twitch
- **Plugins**: AiModPlugin, AutoModPlugin, CreateModRulePlugin, DeleteModRulePlugin, ListModRulesPlugin, ManualBanPlugin, ManualTimeoutPlugin, ManualUnbanPlugin, ModLogPlugin, UpdateModRulePlugin

### `overlays`
- **Table `overlay`**: name (str), config (str), created_at (any), updated_at (any)
- **Endpoints**: DELETE /api/overlays/backgrounds/{filename}, DELETE /api/overlays/{id}, GET /api/overlays, GET /api/overlays/backgrounds, GET /api/overlays/data, GET /api/overlays/{id}, GET /api/overlays/{id}/config, POST /api/overlays, POST /api/overlays/upload-background, PUT /api/overlays/{id}, SSE /api/overlays/stream/{id}
- **Events emitted**: `overlay.config.updated` (overlay_id)
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger, state, twitch
- **Plugins**: CreateOverlayPlugin, DeleteBackgroundPlugin, DeleteOverlayPlugin, GetOverlayPlugin, ListBackgroundsPlugin, ListOverlaysPlugin, OverlayConfigPlugin, OverlayDataPlugin, OverlayStreamPlugin, UpdateOverlayPlugin, UploadBackgroundPlugin

### `ping`
- **Tables**: none
- **Endpoints**: GET /api/ping
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: http, logger
- **Plugins**: PingPlugin

### `stream_state`
- **Table `stream_session`**: twitch_stream_id (Optional[str]), started_at (str), ended_at (Optional[str]), title (Optional[str]), game_name (Optional[str]), peak_viewers (int)
- **Endpoints**: GET /api/stream/sessions, GET /api/stream/status
- **Events emitted**: `stream.session.ended` (ended_at, session_id), `stream.session.started` (broadcaster_login, session_id, started_at, twitch_stream_id)
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: GetStreamStatusPlugin, StreamHistoryPlugin, StreamStateRpcPlugin, StreamStatusPlugin

### `subscribers`
- **Table `subscriber`**: twitch_id (str), login (str), display_name (str), tier (str), is_prime (bool), is_gift (bool), cumulative_months (int), streak_months (Optional[int]), subscribed_at (str), last_sub_at (str), is_active (bool), bits_total (int), last_cheer_at (str)
- **Endpoints**: GET /api/bits/leaderboard, GET /api/gifters/leaderboard, GET /api/subscribers/leaderboard, POST /api/bits/sync, POST /api/subscribers/sync
- **Events emitted**: `subscriber.expired` (twitch_id), `subscriber.gift` (cumulative_total, gifter_id, gifter_name, total), `subscriber.new` (display_name, is_gift, tier, twitch_id), `subscriber.resub` (cumulative_months, display_name, streak_months, tier, twitch_id), `viewer.bits.received` (bits, display_name, twitch_id)
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger, twitch
- **Plugins**: BitsLeaderboardPlugin, BitsTrackerPlugin, GiftersLeaderboardPlugin, SubscribersLeaderboardPlugin, SubscriptionTrackerPlugin, SyncBitsPlugin, SyncSubscribersPlugin

### `system`
- **Tables**: none
- **Endpoints**: GET /api/system/events, GET /api/system/status, GET /api/system/traces/flat, GET /api/system/traces/tree, SSE /api/system/events/stream, SSE /api/system/logs/stream, SSE /api/system/traces/stream
- **Events emitted**: `event.delivery.failed` ()
- **Events consumed**: none
- **Dependencies**: config, db, event_bus, http, logger, registry
- **Plugins**: EventDeliveryMonitorPlugin, SystemEventsPlugin, SystemEventsStreamPlugin, SystemLogsStreamPlugin, SystemStatusPlugin, SystemTracesPlugin, SystemTracesStreamPlugin, ToolHealthPlugin

### `timers`
- **Table `timer`**: name (str), message (str), interval_minutes (int), min_lines (int), enabled (int), last_executed_at (any), created_at (any)
- **Endpoints**: DELETE /api/timers/{id}, GET /api/timers, POST /api/timers, PUT /api/timers/{id}
- **Events emitted**: `timer.created` (id, name), `timer.deleted` (id), `timer.updated` (id)
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: CreateTimerPlugin, DeleteTimerPlugin, GetTimersPlugin, TimerExecutorPlugin, UpdateTimerPlugin

### `tts_chat`
- **Table `tts_voice_config`**: twitch_id (str), twitch_login (str), voice_id (str), voice_name (str), provider (str), created_at (str), updated_at (str), enabled (bool), host (str), port (int), default_voice (str), timeout_s (int), max_message_length (int), skip_commands (bool), skip_links (bool), sub_only (bool), cooldown_seconds (int), blocked_words (str)
- **Endpoints**: DELETE /api/tts/user-voices/{twitch_login}, GET /api/tts/settings, GET /api/tts/user-voices, GET /api/tts/user-voices/{twitch_login}, GET /api/tts/voices, PUT /api/tts/settings, PUT /api/tts/user-voices, SSE /api/tts/overlay/stream
- **Events emitted**: `tts.audio.ready` (audio_b64, text, username, voice_id)
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger, tts, twitch
- **Plugins**: TtsListenerPlugin, TtsRedemptionPlugin, TtsRestoreConfigPlugin, TtsSettingsPlugin, TtsStreamPlugin, TtsUserVoicesPlugin, TtsVoiceCommandPlugin, TtsVoiceListPlugin

### `twitch_auth`
- **Table `twitch_token`**: twitch_id (str), login (str), display_name (str), access_token (str), refresh_token (str), scopes (str), expires_at (str), created_at (str), updated_at (str)
- **Endpoints**: GET /api/auth/twitch, GET /api/auth/twitch/callback, GET /api/auth/twitch/scopes, GET /api/auth/twitch/status, POST /api/auth/twitch/logout
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: config, db, event_bus, http, logger, scheduler, twitch
- **Plugins**: RestoreSessionPlugin, TwitchAuthStatusPlugin, TwitchEventBridgePlugin, TwitchLogoutPlugin, TwitchOAuthCallbackPlugin, TwitchOAuthStartPlugin, TwitchScopesPlugin, TwitchTokenRefreshPlugin

### `viewers`
- **Table `viewer`**: twitch_id (str), login (str), display_name (str), points (int), total_earned (int), is_regular (bool), first_seen (str), last_seen (str)
- **Endpoints**: DELETE /api/viewers/regulars/{twitch_id}, GET /api/viewers, GET /api/viewers/leaderboard, GET /api/viewers/regulars, GET /api/viewers/{login}, POST /api/viewers/regulars, POST /api/viewers/{twitch_id}/points
- **Events emitted**: `viewer.points.awarded` (delta, display_name, twitch_id), `viewer.regular.added` (added_by, display_name, twitch_id), `viewer.regular.removed` (display_name, twitch_id)
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger, twitch
- **Plugins**: AddRegularPlugin, AdjustPointsPlugin, GetViewerPlugin, LeaderboardPlugin, ListRegularsPlugin, ListViewersPlugin, RegularsCommandPlugin, RemoveRegularPlugin, ViewerActivityPlugin

### `webhooks`
- **Table `webhook`**: name (str), url (str), method (str), headers (Optional[str]), body_template (Optional[str]), trigger_type (str), trigger_value (str), filter_field (Optional[str]), filter_value (Optional[str]), enabled (bool), created_at (Optional[str]), updated_at (Optional[str])
- **Endpoints**: DELETE /api/webhooks/{webhook_id}, GET /api/webhooks, POST /api/webhooks, POST /api/webhooks/test, PUT /api/webhooks/{webhook_id}
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, http_client, logger
- **Plugins**: CreateWebhookPlugin, DeleteWebhookPlugin, ListWebhooksPlugin, TestWebhookPlugin, UpdateWebhookPlugin, WebhookExecutorPlugin

