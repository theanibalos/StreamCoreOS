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
            self.logger.error(f"Failed: {e}")
            return {"success": False, "error": str(e)}
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

### 🔧 Tool: `http` (Status: ✅)
```text
HTTP Server Tool (http):
        - PURPOSE: FastAPI-powered HTTP gateway. Supports REST, static files, and WebSockets.
        - HANDLER SIGNATURE: async def execute(self, data: dict, context: HttpContext) -> dict
          'data' = flat merge of path params + query params + body.
          'context' = HttpContext for set_status(), set_cookie(), set_header().
        - CAPABILITIES:
            - add_endpoint(path, method, handler, tags=None, request_model=None,
                           response_model=None, auth_validator=None):
                Buffers a route for registration. Supports Pydantic models for validation
                and OpenAPI schema generation.
                auth_validator: async fn(token: str) -> dict | None
                  → returned payload is injected into data["_auth"].
            - mount_static(path, directory_path): Serve static files.
            - add_ws_endpoint(path, on_connect, on_disconnect=None): WebSocket endpoint.
            - add_sse_endpoint(path, generator, tags=None, auth_validator=None):
                Server-Sent Events endpoint (GET, text/event-stream).
                generator: async generator callable(data: dict) → yields "data: ...

" strings.
                Client disconnect is detected automatically; generator's finally block runs on cleanup.
        - RESPONSE CONTRACT: return {"success": bool, "data": ..., "error": ...}
          Use context.set_status(N) to override HTTP status code (default: 200).
          WARNING: All values in the returned dict must be JSON-serializable (plain dicts,
          lists, str, int, etc.). Pydantic model instances are NOT serializable — always call
          .model_dump() before nesting them: MyModel(...).model_dump()
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
          - await connect(access_token, broadcaster_id, twitch_login):
              Connect EventSub WebSocket + IRC chat. Creates all registered subscriptions.
          - await disconnect(): Disconnect everything.

        CHAT:
          - await send_message(channel, message): Send a chat message.

        HELIX API:
          - await get(endpoint, params?, user_token?): GET to Helix.
          - await post(endpoint, body?, user_token?): POST to Helix.
          - await delete(endpoint, params?, user_token?): DELETE to Helix.
```

### 🔧 Tool: `context_manager` (Status: ✅)
```text
Context Manager Tool (context_manager):
        - PURPOSE: Automatically manages and generates live AI contextual documentation.
        - CAPABILITIES:
            - Reads the system registry.
            - Exports active tools, health status, and domain models to AI_CONTEXT.md.
            - Generates per-domain AI_CONTEXT.md files inside each domain folder.
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

### 🔧 Tool: `state` (Status: ✅)
```text
In-Memory State Tool (state):
        - PURPOSE: Share volatile global data between plugins safely.
        - IDEAL FOR: Counters, temporary caches, and shared business semaphores.
        - CAPABILITIES:
            - set(key, value, namespace='default'): Store a value.
            - get(key, default=None, namespace='default'): Retrieve a value.
            - increment(key, amount=1, namespace='default'): Atomic increment.
            - delete(key, namespace='default'): Delete a key.
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
- **Tables**: ai_config
- **Endpoints**: GET /ai/config, POST /ai/test, PUT /ai/config
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: ai, db, http, logger
- **Plugins**: GetAIConfigPlugin, RestoreAIConfigPlugin, SaveAIConfigPlugin, TestAIConfigPlugin

### `chat_bot`
- **Tables**: chat_command, chat_var
- **Endpoints**: DELETE /chat/commands/{id}, DELETE /chat/vars/{id}, GET /chat/commands, GET /chat/reminders, GET /chat/vars, POST /chat/commands, POST /chat/vars, PUT /chat/commands/{id}, PUT /chat/vars/{id}
- **Events emitted**: chat.command.executed, chat.command.received, chat.message.received
- **Events consumed**: chat.command.received, chat.message.received
- **Dependencies**: ai, db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: ChatAutoResponsePlugin, ChatCommandHandlerPlugin, ChatMessageDispatcherPlugin, ChatStreamPlugin, CommandsListPlugin, CreateCommandPlugin, CreateVarPlugin, DeleteCommandPlugin, DeleteVarPlugin, EchoReminderPlugin, IAChatPlugin, ListCommandsPlugin, ListRemindersPlugin, ListVarsPlugin, UpdateCommandPlugin, UpdateVarPlugin, VarCommandPlugin

### `dashboard`
- **Tables**: channel_stats
- **Endpoints**: GET /dashboard/stats, GET /dashboard/stats/history
- **Events emitted**: dashboard.stats.updated
- **Events consumed**: dashboard.stats.updated, moderation.action.taken, stream.session.ended, stream.session.started, viewer.regular.added, viewer.regular.removed
- **Dependencies**: db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: ChannelStatsCollectorPlugin, ChannelStatsHistoryPlugin, DashboardAlertsPlugin, DashboardStatsPlugin

### `moderation`
- **Tables**: mod_rule
- **Endpoints**: DELETE /moderation/rules/{id}, GET /moderation/log, GET /moderation/rules, POST /moderation/ban, POST /moderation/rules, POST /moderation/timeout, POST /moderation/unban, PUT /moderation/rules/{id}
- **Events emitted**: moderation.action.taken, moderation.rules.updated
- **Events consumed**: chat.message.received, moderation.rules.updated
- **Dependencies**: ai, db, event_bus, http, logger, state, twitch
- **Plugins**: AiModPlugin, AutoModPlugin, CreateModRulePlugin, DeleteModRulePlugin, ListModRulesPlugin, ManualBanPlugin, ManualTimeoutPlugin, ManualUnbanPlugin, ModLogPlugin, UpdateModRulePlugin

### `ping`
- **Tables**: none
- **Endpoints**: GET /ping
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: http, logger
- **Plugins**: PingPlugin

### `stream_state`
- **Tables**: stream_session
- **Endpoints**: GET /stream/sessions, GET /stream/status
- **Events emitted**: stream.session.ended, stream.session.started
- **Events consumed**: stream.status.requested
- **Dependencies**: db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: GetStreamStatusPlugin, StreamHistoryPlugin, StreamStateRpcPlugin, StreamStatusPlugin

### `system`
- **Tables**: none
- **Endpoints**: GET /system/events, GET /system/status, GET /system/traces/flat, GET /system/traces/tree
- **Events emitted**: event.delivery.failed
- **Events consumed**: none
- **Dependencies**: config, db, event_bus, http, logger, registry
- **Plugins**: EventDeliveryMonitorPlugin, SystemEventsPlugin, SystemEventsStreamPlugin, SystemLogsStreamPlugin, SystemStatusPlugin, SystemTracesPlugin, SystemTracesStreamPlugin, ToolHealthPlugin

### `timers`
- **Tables**: timer
- **Endpoints**: DELETE /timers/{id}, GET /timers, POST /timers, PUT /timers/{id}
- **Events emitted**: timer.created, timer.deleted, timer.updated
- **Events consumed**: chat.message.received, timer.created, timer.deleted, timer.updated
- **Dependencies**: db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: CreateTimerPlugin, DeleteTimerPlugin, GetTimersPlugin, TimerExecutorPlugin, UpdateTimerPlugin

### `tts_chat`
- **Tables**: tts_voice_config
- **Endpoints**: DELETE /tts/user-voices/{twitch_login}, GET /tts/settings, GET /tts/user-voices, GET /tts/user-voices/{twitch_login}, GET /tts/voices, PUT /tts/settings, PUT /tts/user-voices
- **Events emitted**: tts.audio.ready
- **Events consumed**: chat.message.received, tts.audio.ready
- **Dependencies**: db, event_bus, http, logger, tts, twitch
- **Plugins**: TtsListenerPlugin, TtsRedemptionPlugin, TtsRestoreConfigPlugin, TtsSettingsPlugin, TtsStreamPlugin, TtsUserVoicesPlugin, TtsVoiceCommandPlugin, TtsVoiceListPlugin

### `twitch_auth`
- **Tables**: twitch_token
- **Endpoints**: GET /auth/twitch, GET /auth/twitch/callback, GET /auth/twitch/status
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger, scheduler, twitch
- **Plugins**: RestoreSessionPlugin, TwitchAuthStatusPlugin, TwitchOAuthCallbackPlugin, TwitchOAuthStartPlugin, TwitchTokenRefreshPlugin

### `twitch_redemptions`
- **Tables**: redemption
- **Endpoints**: none
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: logger, twitch
- **Plugins**: HackThePlanetPlugin

### `viewers`
- **Tables**: viewer
- **Endpoints**: DELETE /viewers/regulars/{twitch_id}, GET /viewers, GET /viewers/leaderboard, GET /viewers/regulars, GET /viewers/{login}, POST /viewers/regulars, POST /viewers/{twitch_id}/points
- **Events emitted**: viewer.points.awarded, viewer.regular.added, viewer.regular.removed
- **Events consumed**: chat.command.received, chat.message.received
- **Dependencies**: db, event_bus, http, logger, twitch
- **Plugins**: AddRegularPlugin, AdjustPointsPlugin, GetViewerPlugin, LeaderboardPlugin, ListRegularsPlugin, ListViewersPlugin, RegularsCommandPlugin, RemoveRegularPlugin, ViewerActivityPlugin

