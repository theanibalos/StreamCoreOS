# 📜 SYSTEM MANIFEST

> This file is ALL you need to build a plugin. For advanced topics (testing, observability, creating tools), see [INSTRUCTIONS_FOR_AI.md](INSTRUCTIONS_FOR_AI.md).

## ⚡ Operating Context
This file contains the technical signature of active tools and domains in the system.
For plugin development guides, critical rules, and syntax examples, see [AGENTS.md](AGENTS.md).

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

### 🔧 Tool: `http` (Status: ✅)
```text
HTTP Server Tool (http):
        - PURPOSE: FastAPI-powered HTTP gateway. Supports REST, static files, WebSockets and SSE.
        - HANDLER SIGNATURE: async def execute(self, data: dict, context: HttpContext) -> dict
          'data' = flat merge of [path params] + [query params] + [body/form fields].
          Special keys in 'data':
            - data["_auth"]: contains the payload from auth_validator if successful.
            - data["_files"]: list of UploadedFile objects (only if has_files=True).
                Fields: .filename, .content_type, .stream (sync file object), await .read().
        - SECURITY DEFAULTS:
            - Cookies set via context.set_cookie are 'Secure=True', 'HttpOnly=True', 'SameSite=Lax'.
            - CSRF Guard: Mutations (POST/PUT/DELETE) using cookie auth REQUIRE 'X-Requested-With' header.
            - Swagger UI (/docs): endpoints with auth_validator show a lock icon and accept
              tokens via the "Authorize" button (documentation-only; real check unaffected).
        - CAPABILITIES:
            - add_endpoint(path, method, handler, tags=None, request_model=None,
                           response_model=None, auth_validator=None, has_files=False):
                - has_files: if True, enables multipart/form-data. Request model fields 
                  become Form fields. To use a file: file = data["_files"][0]; 
                  await s3.upload_fileobj(file.filename, file.file, content_type=file.content_type)
            - mount_static(path, directory_path, html=False, allow_extensions=None):
                Serve static files from a directory. Deny by default: only files whose
                extension is allowed are served (default DEFAULT_STATIC_EXTENSIONS; pass
                a set to declare your own, or "*" to serve everything). Dotfiles are
                always refused except under '.well-known/'. Use html=True to serve
                index.html for directory requests, which a UI/SPA mounted at "/" needs.
                Raises ValueError if the directory does not exist.
            - add_ws_endpoint(path, on_connect, on_disconnect=None, auth_validator=None):
                WebSocket support. on_connect receives a WebSocketConnection: send_text,
                send_json, receive_text, receive_json, close, query_params, path_params.
                With auth_validator the token is read from the Authorization header, the
                `token` query param, then the access_token cookie; an invalid one is
                closed with 1008 BEFORE the handshake and on_connect takes (conn, payload).
            - add_sse_endpoint(path, generator, tags=None, auth_validator=None):
                Server-Sent Events. generator yields formatted strings: "data: {...}\n\n".
            - register_pre_mount_hook(hook): hook(endpoints: list[dict]) is called once in
                on_boot_complete(), before routes are mounted, with every buffered endpoint
                (method, path, owner) — the first point where all plugins' add_endpoint()
                calls are guaranteed to have run. Used for boot-time checks across ALL
                registered routes (e.g. the architecture linter's route-collision scan).
        - HttpContext CAPABILITIES (inside handler):
            - context.set_status(code: int): Override HTTP status. Default is 200 on
              success:true; 400 on success:false unless set_status() is called.
            - context.redirect(url: str, status=302): Redirect to another URL.
            - context.set_cookie(key, value, max_age=3600, ...): Set secure response cookie.
            - context.set_header(key, value): Add custom response header.
            - context.set_binary_response(content: bytes, media_type: str): Return raw file.
            - context.raw_body: Exact inbound HTTP request body bytes. Use for webhook
              signature verification; providers sign bytes, not a re-serialized dict.
            - context.get_header(key, default=None): Read inbound request headers
              case-insensitively (e.g. X-Signature for signed webhooks).
            - context.client_ip: Best-effort caller IP (property). Raw signal only — the
              plugin decides what to do with it (e.g. state.increment() keyed by IP for
              an identity-aware business rule). Never security-authoritative on its own;
              see context.py's client_ip docstring for the trust order and its limits.
        - RESPONSE CONTRACT:
            - Standard: return {"success": bool, "data": ..., "error": ...}
            - WARNING: All values in 'data' must be JSON-serializable. Pydantic model 
              instances are NOT serializable — always call .model_dump() before returning.
```

### 🔧 Tool: `telemetry` (Status: ✅)
```text
Telemetry Tool (telemetry):
        - PURPOSE: OpenTelemetry distributed tracing AND metrics. Auto-instruments all tool
          calls via ToolProxy. No changes needed in plugins or existing tools to get basic
          spans or metrics.
        - ACTIVATION: Set OTEL_ENABLED=true. Degrades gracefully if disabled or packages missing.
        - ENV VARS:
            - OTEL_ENABLED: "true" to activate (default: "false").
            - OTEL_SERVICE_NAME: Service name in traces/metrics (default: "microcoreos").
            - OTEL_EXPORTER_OTLP_ENDPOINT: OTLP/gRPC endpoint (e.g. "http://otel-collector:4317").
              If not set, traces and metrics are printed to console (development mode).
        - CAPABILITIES:
            - get_tracer(scope: str) -> Tracer: Named tracer for custom spans inside a plugin.
                Usage: tracer = self.telemetry.get_tracer("my_plugin")
                       with tracer.start_as_current_span("my_operation"): ...
                Returns a no-op tracer if OTel is disabled — safe to use unconditionally.
            - get_meter(scope: str) -> Meter: Named meter for custom metrics inside a plugin.
                Usage: meter = self.telemetry.get_meter("my_plugin")
                       counter = meter.create_counter("orders_created")
                       counter.add(1)
                Returns a no-op meter if OTel is disabled — safe to use unconditionally.
        - AUTO-INSTRUMENTATION (zero config):
            Every tool call (db.execute, event_bus.publish, auth.create_token, etc.)
            gets a span automatically via ToolProxy, AND is recorded as an OTel histogram
            (tool_call_duration_ms) and counter (tool_call_total) with tool/method/success
            attributes — the same record already exposed at registry.get_metrics() / GET
            /system/metrics, now also exported over OTLP. No plugin changes needed.
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
          - consume_state(state) -> bool:
              Validate and consume a CSRF state generated by get_auth_url().
              Returns True once per state (second call with the same state returns False).
              Use it in the OAuth callback before calling exchange_code().
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
          - get_session() -> dict | None:
              Returns the current active session or None if not connected.
              Keys: access_token, refresh_token, broadcaster_id, login.
              Use it to check session state or read tokens without storing them elsewhere.
          - is_eventsub_connected() -> bool:
              Returns True only when the EventSub WebSocket session is established.
              Use it to check if events are actually flowing.
          - is_connecting() -> bool:
              Returns True when a token is set but EventSub hasn't connected yet.
              Use it to show a "connecting..." state between connect() and full readiness.

        CHAT:
          - await send_message(channel, message): Send a chat message.

        HELIX API:
          - await get(endpoint, params?, user_token?): GET to Helix.
          - await post(endpoint, body?, user_token?): POST to Helix.
          - await delete(endpoint, params?, user_token?): DELETE to Helix.
```

### 🔧 Tool: `youtube` (Status: ✅)
```text
YouTube Tool (youtube):
        - PURPOSE: Integration with YouTube Data API v3, Google OAuth 2.0, and high-performance Live Chat via gRPC streamList.
        - ENVIRONMENT:
            - YOUTUBE_CLIENT_ID: Google Cloud OAuth 2.0 Client ID.
            - YOUTUBE_CLIENT_SECRET: Google Cloud OAuth 2.0 Client Secret.
            - YOUTUBE_REDIRECT_URI: OAuth redirect callback URL (default: http://localhost/api/auth/youtube/callback).
        - OAUTH & SESSION LIFECYCLE:
            - require_scopes(scopes: list[str]) -> None: Declare needed OAuth scopes in on_boot().
            - get_required_scopes() -> list[str]: Returns list of accumulated required scopes.
            - get_auth_url() -> tuple[str, str]: Returns (authorization_url, csrf_state_token).
            - consume_state(state: str) -> bool: Validates and consumes CSRF state from callback.
            - await exchange_code(code: str) -> dict: Exchanges authorization code for tokens dictionary.
            - await refresh_user_token(refresh_token: str) -> dict: Refreshes access token with Google OAuth.
            - await connect(access_token, refresh_token, channel_id, channel_title, expires_in=3600) -> None:
                Establishes the active authenticated session.
            - await disconnect() -> None: Clears active session.
            - get_session() -> dict | None: Returns active session metadata (tokens, channel_id, channel_title).
            - is_connected() -> bool: Returns True if active session is connected and authenticated.
        - BROADCAST & CHAT DISCOVERY:
            - await get_user_info() -> dict: Returns channel details: {"id": channel_id, "title": channel_title}.
            - await get_active_broadcast() -> dict | None: Fetches currently active or testing live broadcast.
            - await get_live_chat_id() -> str | None: Extracts active liveChatId for the current live stream.
        - LIVE CHAT & MODERATION:
            - await stream_chat_messages(live_chat_id: str, page_token: str | None = None, max_results: int = 500):
                Async generator yielding real-time chat messages via gRPC streamList (recommended).
            - await list_chat_messages(live_chat_id: str, page_token: str | None = None, max_results: int = 500) -> dict:
                REST polling fallback for live chat messages.
            - await send_message(live_chat_id: str, text: str) -> dict:
                Sends a chat message to YouTube Live Chat (requires 'https://www.googleapis.com/auth/youtube.force-ssl').
            - await delete_message(message_id: str) -> dict: Deletes a chat message by message_id.
            - await ban_user(live_chat_id: str, channel_id: str, duration_s: int | None = None) -> dict:
                Bans (permanent) or timeouts (temporary, duration_s) a channel in live chat.
        - RAW HTTP GATEWAY (with auto-refreshing Bearer token):
            - await get(endpoint: str, params: dict | None = None) -> dict
            - await post(endpoint: str, body: dict | None = None, params: dict | None = None) -> dict
            - await delete(endpoint: str, params: dict | None = None) -> dict
```

### 🔧 Tool: `stream_tool` (Status: ✅)
```text
Stream Tool (stream_tool): emisión/restream centralizada.
        Si existe rtmp_engine, usa RTMP ingest local + relays FFmpeg por pipe.
        Si no existe, usa FFmpeg leyendo STREAM_INPUT_URL.
        Entrada OBS: rtmp://localhost:1935/live/{obs_stream_key}.
        Métodos: start_output(id), stop_output(id), start_active_outputs(), stop_active_outputs(), set_fallback_video(path), get_runtime_status().
```

### 🔧 Tool: `context_manager` (Status: ✅)
```text
Context Manager Tool (context_manager):
        - PURPOSE: Automatically manages and generates live AI contextual documentation.
        - CAPABILITIES:
            - Reads the system registry.
            - Exports active tools, health status, and domain models to AI_CONTEXT.md.
            - Embeds the plugin authoring guide (tools/context/authoring_guide.md):
              executor rules plus one complete template per deliverable type, so the
              manifest alone is enough to write a plugin or its tests.
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
Key-Value State Tool (state):
        - PURPOSE: Share volatile global data between plugins safely.
        - IDEAL FOR: Counters, temporary caches, rate-limit windows, business semaphores.
        - CONTRACT: All methods are async. Values must be JSON-serializable so the
          tool can be swapped for a distributed store (Redis) without touching plugins.
        - TTL: optional expiry in seconds. Expired keys behave like missing keys.
          On increment(), the TTL only applies when the key is created (fixed window).
        - CAPABILITIES:
            - await set(key, value, namespace='default', ttl=None): Store a value.
            - await get(key, default=None, namespace='default'): Retrieve a value (None if missing).
            - await has(key, namespace='default'): Returns True if key exists.
            - await keys(namespace='default'): Returns list of all live keys in the namespace.
            - await get_all(namespace='default'): Returns a deep copy of all live key-value pairs.
            - await increment(key, amount=1, namespace='default', ttl=None): Atomic increment.
              Starts at 0. Returns the new value.
            - await delete(key, namespace='default'): Delete a key (no-op if missing).
            - await clear(namespace='default'): Remove all keys in the namespace.
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
                NOTE: status is updated REACTIVELY via ToolProxy (hybrid policy):
                ToolUnavailableError -> DEAD immediately; any other exception ->
                DEAD only after 5 consecutive failures (success resets the streak).
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
            - add_interval_job(seconds: float, callback, job_id?: str, *, minutes, hours,
                               max_instances=1, coalesce=True, misfire_grace_time=1) -> str:
                Schedule a recurring job on a fixed interval. Use this for sub-minute
                rates, which a 5-field cron expression cannot express (its unit is the
                minute). seconds accepts fractions: 0.25 = 4x/second.
                At max_instances=1 a run that overlaps the previous one is DROPPED, and
                a run later than misfire_grace_time is DROPPED — silently, as far as the
                callback is concerned. Both are logged as "Run DROPPED". Raise
                max_instances if the job must not skip.
            - add_one_shot(run_at: datetime, callback, job_id?: str) -> str:
                Schedule a one-time job at a specific datetime (timezone-aware).
                Returns job_id. IN-MEMORY: lost if the process restarts before firing.
                For one-shots that must survive restarts, publish to the bus:
                "scheduler.one_shot.schedule" (durable scheduling service — install extras/available_domains/scheduler).
            - remove_job(job_id: str) -> bool:
                Remove a job by ID. Returns True if removed, False if not found.
            - list_jobs() -> list[dict]:
                Snapshot of all scheduled jobs: [{id, next_run, trigger}].
        - REGISTER IN on_boot(): jobs are collected during on_boot(), scheduler starts
          in on_boot_complete() after all plugins have registered.
        - SCALING (N replicas): set SCHEDULER_ENABLED=false in worker replicas — jobs
          register everywhere but fire only in the single "beat" replica. Jobs should
          publish an event to the bus and return; workers consume it (group semantics
          guarantee exactly one execution across the fleet). Do heavy work in the
          worker, never in the job callback.
        - SWAP: replace with Celery beat by creating a new tool with name = "scheduler"
          and the same 4-method API. Plugins do not change.
```

### 🔧 Tool: `rtmp_engine` (Status: ✅)
```text
RTMP Engine Tool (rtmp_engine):
        - PURPOSE: Standalone RTMP Ingress Server (Port 1935) + FFmpeg Passthrough Relays.
        - CAPABILITIES:
            - is_ffmpeg_available() -> bool: Returns True if FFmpeg binary is installed.
            - is_obs_connected() -> bool: Returns True if OBS is currently streaming to server.
            - set_obs_connected(connected, stream_key, flv_header) -> None: Updates OBS connection state.
            - set_fallback_config(config) -> None: Configure standby mode assets.
            - cache_metadata(chunk) -> None: Cache FLV metadata tag.
            - cache_avc_config(chunk) -> None: Cache H.264 sequence header.
            - cache_aac_config(chunk) -> None: Cache AAC sequence header.
            - start_relay(dest_id, rtmp_url, stream_key, platform) -> dict: Spawns relay.
            - stop_relay(dest_id) -> bool: Terminates relay process.
            - stop_all_relays() -> int: Terminates all active relay processes.
            - get_relay_status(dest_id) -> dict: Get current status of a specific relay.
            - get_all_relays() -> dict: Get status of all relays.
            - get_source_status() -> dict: Current source: obs, fallback, or waiting.
            - broadcast_flv_bytes(chunk, from_obs=False) -> None: Forward FLV bytes to all relays.
            - close() -> None: Gracefully close RTMP server and relays.
```

### 🔧 Tool: `event_bus` (Status: ✅)
```text
Universal Event Bus (event_bus):
        - publish(event_name, data, **kwargs): Broadcast an event.
        - subscribe(event_name, callback, group=None, retries=0, backoff=0.5, broadcast=False):
          Listen for events. group=None derives a STABLE group from the callback identity:
          replicas of the same plugin consume each event exactly once across the fleet,
          while distinct plugins each get their own copy. Use group="pool" for explicit
          worker pools, broadcast=True ONLY for instance-local concerns (every replica
          receives a copy — e.g. local cache invalidation).
        - request(event_name, data, timeout=5): Async RPC (returns dict).
        - unsubscribe(event_name, callback): Stop listening.
        - get_trace_history() -> List[TraceNode]: Last 500 event records.
        - get_subscribers() -> dict: Current subscriber map.
        - add_listener(callback): Sink for all events (record: dict).
        - add_failure_listener(callback): Sink for errors (record: dict).
        
        CRITICAL: Subscribing callbacks receive the event envelope as their single
        argument — read event.payload. Leave the parameter untyped (no annotation,
        no import needed): async def on_event(self, event): print(event.payload)
        
        RETRIES & IDEMPOTENCY:
        - If 'retries' > 0, the handler will be re-executed on failure with exponential backoff.
        - Ensure handlers are idempotent as they may run multiple times.

        DEAD-LETTER QUEUE (DLQ):
        - Final failures are published to '_dlq.<original_event>'.
        - Payload includes 'original' envelope, 'subscriber', 'error', and 'attempts'.
        - Loop protection: '_dlq.*' and '_reply.*' events are never dead-lettered.
        - Toggle via EVENT_BUS_DLQ_ENABLED (default: true).

        UNIVERSAL CAPABILITIES (kwargs):
        - key: String. Strict ordering PER KEY. Without a key, do NOT assume
          cross-event ordering: it varies by transport (total in-process,
          partition-dependent on Kafka).
        - priority: Integer (1-10). Importance (RabbitMQ).
        - delay: Integer (seconds). Delivery schedule. Crash-safe only when
          the active transport claims delay=native (see ACTIVE TRANSPORT).
        - ttl: Float (seconds). Message expiration hint. Counted from PUBLISH
          time and therefore INCLUDES any delay (delay=60 + ttl=30 expires
          before it can ever be delivered).
        - correlation_id: String. Cross-reference for RPC.

        RESILIENCE:
        - A subscriber that reaches 5 consecutive FINAL failures for a specific event is auto-unsubscribed.
        - Each auto-unsubscribe publishes 'system.subscriber.dropped'
          (payload: event, subscriber, error, consecutive_failures) so the drop
          is observable — subscribe to it for alerting/monitoring.

        ACTIVE TRANSPORT: RabbitMQDriver — capability claims: {'delay': 'native', 'retries': 'in_bus', 'dlq': 'in_bus'}
        ("native" = the broker implements it, crash-safe; "in_bus" = software
        fallback in this process' memory).
```

### 🔧 Tool: `db` (Status: ✅)
```text
Async SQLite Persistence Tool (sqlite):
        - PURPOSE: PostgreSQL-compatible relational storage (drop-in swap at the
          TOOL-API level: same methods, same placeholders). Accepts PostgreSQL-style
          placeholders ($1, $2...) and converts them transparently to SQLite's
          native '?'. SQL text itself is NEVER dialect-translated.
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
            - await describe_schema() → dict: Live schema of the active database:
              {table: {internal, columns, unique, foreign_keys}}.
              Column types are normalized to a closed vocabulary
              (text/int/float/bool/timestamp/json/blob) so the same migration
              yields the same description on any engine.
              Tables whose name starts with "_" are marked internal;
              engine-owned tables are excluded.
        - EXCEPTIONS: Raises DatabaseError or DatabaseConnectionError on failure.
          Every DatabaseError carries a CLASSIFIED, engine-independent contract:
            - kind: one of unique_violation / foreign_key_violation /
              not_null_violation / check_violation / unknown (CLOSED vocabulary —
              the same values on any engine, so the swap keeps behavior).
            - table / columns: the target of the violation, filled in only where
              every engine can report it (unique and NOT NULL); FOREIGN KEY and
              CHECK carry kind only.
          Branch on the kind, NEVER on str(e) — the message text is engine-specific:
            except Exception as e:
                if getattr(e, "kind", None) == "unique_violation": ...
        - MIGRATIONS: SQL files in domains/*/migrations/*.sql are auto-applied on boot via
          topological sort (alphabetical by default). Migrations run VERBATIM (no
          dialect translation). Engine-specific SQL commits you to that engine;
          portable SQL (e.g. CURRENT_TIMESTAMP, not NOW()) keeps the
          SQLite <-> PostgreSQL swap free. To declare that one migration must
          run before another, add as the first comment line:
            "-- depends: other_domain/001_file.sql"
          Works for same-domain or cross-domain dependencies. .sql extension is optional.
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
- **Table `ai_config`** (storage): id (int, PK), updated_at (text, NOT NULL, default datetime('now')), chat_cooldown_s (int, NOT NULL, default 120), chat_system_prompt (text, NOT NULL, default 'You are a helpful Twitch chat assistant. Be concise and reply in under 40 words.'), chat_max_tokens (int, NOT NULL, default 200), chat_temperature (float, NOT NULL, default 0.7), chat_ia_enabled (int, NOT NULL, default 1), active_provider_id (int) — FK active_provider_id → ai_providers.id
- **Table `ai_providers`** (storage): id (int, PK), name (text, NOT NULL), provider (text, NOT NULL), endpoint_url (text, NOT NULL), api_key (text, NOT NULL, default ''), model (text, NOT NULL), timeout_s (int, NOT NULL, default 120), disable_reasoning (int, NOT NULL, default 0), extra_headers (text, NOT NULL, default '{}'), extra_payload (text, NOT NULL, default '{}'), created_at (text, NOT NULL, default datetime('now')), updated_at (text, NOT NULL, default datetime('now'))
- **Model `AIConfig`** (domain vocabulary): id: int, provider: str, endpoint_url: str, model: str, updated_at: str
- **Endpoints**:
  - `DELETE /api/ai/providers/{provider_id}`
    - **res**: success: bool, error: Optional[str]
  - `GET /api/ai/config`
    - **res**: AIConfigData(provider: str, endpoint_url: str, model: str, has_api_key: bool, timeout_s: int, disable_reasoning: bool, extra_headers: dict, extra_payload: dict, chat_cooldown_s: int, chat_system_prompt: str, chat_max_tokens: int, chat_temperature: float, updated_at: Optional[str])
  - `GET /api/ai/ia/enabled`
    - **res**: IAEnabledData(enabled: bool)
  - `GET /api/ai/providers`
    - **res**: List[AIProviderEntry(id: int, name: str, provider: str, endpoint_url: str, model: str, has_api_key: bool, timeout_s: int, disable_reasoning: bool, extra_headers: dict, extra_payload: dict, is_active: bool, updated_at: str)]
  - `POST /api/ai/providers`
    - **req**: name: str, provider: str, endpoint_url: str, model: str, api_key: str, timeout_s: int, disable_reasoning: bool, extra_headers: dict, extra_payload: dict
    - **res**: AIProviderData(id: int, name: str, provider: str, endpoint_url: str, model: str, has_api_key: bool, timeout_s: int, disable_reasoning: bool, extra_headers: dict, extra_payload: dict, is_active: bool, updated_at: str)
  - `POST /api/ai/providers/test`
    - **req**: provider_id: Optional[int], provider: str, endpoint_url: str, model: str, api_key: str, timeout_s: int, disable_reasoning: bool, extra_headers: dict, extra_payload: dict
    - **res**: dict
  - `POST /api/ai/providers/{provider_id}/activate`
    - **res**: success: bool, error: Optional[str]
  - `POST /api/ai/providers/{provider_id}/test`
    - **res**: dict
  - `POST /api/ai/test`
    - **res**: dict
  - `PUT /api/ai/config`
    - **req**: chat_cooldown_s: int, chat_system_prompt: str, chat_max_tokens: int, chat_temperature: float
    - **res**: AIConfigData(provider: Optional[str], endpoint_url: Optional[str], model: Optional[str], has_api_key: bool, timeout_s: Optional[int], disable_reasoning: Optional[bool], extra_headers: dict, extra_payload: dict, chat_cooldown_s: int, chat_system_prompt: str, chat_max_tokens: int, chat_temperature: float, updated_at: Optional[str])
  - `PUT /api/ai/ia/enabled`
    - **req**: enabled: bool
    - **res**: IAEnabledData(enabled: bool)
  - `PUT /api/ai/providers/{provider_id}`
    - **res**: success: bool, error: Optional[str]
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: ai, db, http, logger, state
- **Plugins**: ai_config.ActivateAIProviderPlugin, ai_config.CreateAIProviderPlugin, ai_config.DeleteAIProviderPlugin, ai_config.GetAIConfigPlugin, ai_config.ListAIProvidersPlugin, ai_config.RestoreAIConfigPlugin, ai_config.SaveAIConfigPlugin, ai_config.TestAIConfigPlugin, ai_config.TestAIProviderConfigPlugin, ai_config.TestAIProviderPlugin, ai_config.ToggleIAChatPlugin, ai_config.UpdateAIProviderPlugin

### `chat_bot`
- **Table `chat_commands`** (storage): id (int, PK), name (text, NOT NULL), response (text, NOT NULL), cooldown_s (int, NOT NULL, default 30), enabled (int, NOT NULL, default 1), created_at (text, NOT NULL, default datetime('now')), userlevel (text, NOT NULL, default 'everyone'), use_count (int, NOT NULL, default 0), global_cooldown_s (int, NOT NULL, default 0), action (text) — UNIQUE(name)
- **Table `chat_log`** (storage): id (int, PK), channel (text, NOT NULL), user_id (text, NOT NULL), display_name (text, NOT NULL), message (text, NOT NULL), is_command (int, NOT NULL, default 0), timestamp (text, NOT NULL), platform (text, NOT NULL, default 'twitch'), source_message_id (text)
- **Table `chat_vars`** (storage): id (int, PK), name (text, NOT NULL), value (text, NOT NULL, default '0'), enabled (int, NOT NULL, default 1), created_at (text, NOT NULL, default datetime('now')) — UNIQUE(name)
- **Model `ChatCommandEntity`** (domain vocabulary): id: int, name: str, response: str, cooldown_s: int, enabled: int, created_at: str, action: Optional[str]
- **Model `ChatLogEntity`** (domain vocabulary): id: int, channel: str, user_id: str, display_name: str, message: str, is_command: int, timestamp: str
- **Model `ChatVarEntity`** (domain vocabulary): id: int, name: str, value: str, enabled: int, created_at: str
- **Endpoints**:
  - `DELETE /api/chat/commands/{id}`
    - **res**: dict
  - `DELETE /api/chat/vars/{id}`
    - **res**: dict
  - `GET /api/chat/badges`
  - `GET /api/chat/commands`
    - **res**: list[CommandData(id: int, name: str, response: str, cooldown_s: int, global_cooldown_s: int, userlevel: str, use_count: int, enabled: bool, action: Optional[str])]
  - `GET /api/chat/reminders`
    - **res**: list[ReminderData(job_id: str, message: str, run_at: str, scheduled_by: str, channel: str)]
  - `GET /api/chat/vars`
    - **res**: List[VarData(id: int, name: str, value: str, enabled: bool)]
  - `POST /api/chat/commands`
    - **req**: name: str, response: str, cooldown_s: int, global_cooldown_s: int, userlevel: str, action: Optional[str]
    - **res**: CommandData(id: int, name: str, response: str, cooldown_s: int, global_cooldown_s: int, userlevel: str, use_count: int, enabled: bool, action: Optional[str])
  - `POST /api/chat/vars`
    - **req**: name: str, value: str
    - **res**: VarData(id: int, name: str, value: str, enabled: bool)
  - `PUT /api/chat/commands/{id}`
    - **req**: response: Optional[str], cooldown_s: Optional[int], global_cooldown_s: Optional[int], userlevel: Optional[str], enabled: Optional[bool]
    - **res**: CommandData(id: int, name: str, response: str, cooldown_s: int, global_cooldown_s: int, userlevel: str, use_count: int, enabled: bool, action: Optional[str])
  - `PUT /api/chat/vars/{id}`
    - **req**: value: Optional[str], enabled: Optional[bool]
    - **res**: VarData(id: int, name: str, value: str, enabled: bool)
  - `SSE /api/chat/stream`
- **Events emitted**: `chat.command.executed` (channel, command, display_name, user_id), `chat.command.received` (args, command), `chat.message.received` (), `chat.message.send` (channel_id, channel_name, message, platform)
- **Events consumed**: chat.command.received, chat.message.received, message.resend
- **Dependencies**: ai, db, event_bus, http, logger, scheduler, state, twitch, youtube
- **Plugins**: chat_bot.ChatAutoResponsePlugin, chat_bot.ChatBadgesPlugin, chat_bot.ChatCommandHandlerPlugin, chat_bot.ChatMessageDispatcherPlugin, chat_bot.ChatStreamPlugin, chat_bot.CommandsListPlugin, chat_bot.CreateCommandPlugin, chat_bot.CreateVarPlugin, chat_bot.DeleteCommandPlugin, chat_bot.DeleteVarPlugin, chat_bot.EchoReminderPlugin, chat_bot.IAChatPlugin, chat_bot.ListCommandsPlugin, chat_bot.ListRemindersPlugin, chat_bot.ListVarsPlugin, chat_bot.MessageResendPlugin, chat_bot.UpdateCommandPlugin, chat_bot.UpdateVarPlugin, chat_bot.VarCommandPlugin

### `chat_platform`
- **Tables**: none
- **Endpoints**: none
- **Events emitted**: none
- **Events consumed**: chat.message.send
- **Dependencies**: event_bus, logger, twitch, youtube
- **Plugins**: chat_platform.TwitchChatSendPlugin, chat_platform.YouTubeChatSendPlugin

### `dashboard`
- **Table `channel_stats`** (storage): id (int, PK), recorded_at (text, NOT NULL, default datetime('now')), viewer_count (int, NOT NULL, default 0), follower_count (int, NOT NULL, default 0)
- **Model `ChannelStatsEntity`** (domain vocabulary): id: int, recorded_at: str, viewer_count: int, follower_count: int
- **Endpoints**:
  - `GET /api/dashboard/stats`
    - **res**: DashboardStatsData(stream: StreamInfo(online: bool, started_at: Optional[str], viewer_count: Optional[int], follower_count: Optional[int]), top_viewers: list[TopViewer(global_user_id: str, platform: str, platform_user_id: str, display_name: str, points: int)], recent_mod_actions: list[RecentModAction(display_name: str, action: str, reason: str, created_at: str)], total_viewers: int)
  - `GET /api/dashboard/stats/history`
    - **res**: list[StatsSnapshot(id: int, recorded_at: str, viewer_count: int, follower_count: int)]
  - `POST /api/dashboard/alerts/test`
    - **req**: event_type: str, data: Optional[dict]
    - **res**: TestAlertData(event_type: str)
  - `SSE /api/dashboard/alerts`
- **Events emitted**: `dashboard.stats.updated` (follower_count, viewer_count)
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: dashboard.ChannelStatsCollectorPlugin, dashboard.ChannelStatsHistoryPlugin, dashboard.DashboardAlertsPlugin, dashboard.DashboardStatsPlugin

### `moderation`
- **Table `mod_log`** (storage): id (int, PK), twitch_id (text, NOT NULL), display_name (text, NOT NULL), action (text, NOT NULL), reason (text, NOT NULL), rule_id (int), created_at (text, NOT NULL, default datetime('now')), platform (text, NOT NULL, default 'twitch'), channel_id (text), user_id (text, NOT NULL, default '')
- **Table `mod_rules`** (storage): id (int, PK), type (text, NOT NULL), value (text), action (text, NOT NULL, default 'timeout'), duration_s (int), enabled (int, NOT NULL, default 1), created_at (text, NOT NULL, default datetime('now')), exempt_roles (text, NOT NULL, default '')
- **Model `ModRuleEntity`** (domain vocabulary): id: int, type: str, value: Optional[str], action: str, duration_s: Optional[int], enabled: int, exempt_roles: str
- **Model `ModLogEntity`** (domain vocabulary): id: int, twitch_id: str, display_name: str, action: str, reason: str, rule_id: Optional[int], created_at: str
- **Endpoints**:
  - `DELETE /api/moderation/rules/{id}`
    - **res**: dict
  - `GET /api/moderation/log`
    - **res**: list[ModLogEntry(id: int, platform: str, channel_id: Optional[str], user_id: str, twitch_id: Optional[str], display_name: str, action: str, reason: str, rule_id: Optional[int], created_at: str)]
  - `GET /api/moderation/rules`
    - **res**: list[ModRuleData(id: int, type: str, value: Optional[str], action: str, duration_s: Optional[int], enabled: bool, exempt_roles: list[str])]
  - `POST /api/moderation/ban`
    - **req**: platform: str, channel_id: Optional[str], user_id: Optional[str], twitch_id: Optional[str], display_name: Optional[str], reason: Optional[str]
    - **res**: dict
  - `POST /api/moderation/rules`
    - **req**: type: str, value: Optional[str], action: str, duration_s: Optional[int], exempt_roles: list[str]
    - **res**: ModRuleData(id: int, type: str, value: Optional[str], action: str, duration_s: Optional[int], enabled: bool, exempt_roles: list[str])
  - `POST /api/moderation/timeout`
    - **req**: platform: str, channel_id: Optional[str], user_id: Optional[str], twitch_id: Optional[str], display_name: Optional[str], duration_s: int, reason: Optional[str]
    - **res**: dict
  - `POST /api/moderation/unban`
    - **req**: platform: str, channel_id: Optional[str], user_id: Optional[str], twitch_id: Optional[str], display_name: Optional[str]
    - **res**: dict
  - `PUT /api/moderation/rules/{id}`
    - **req**: value: Optional[str], action: Optional[str], duration_s: Optional[int], enabled: Optional[bool], exempt_roles: Optional[list[str]]
    - **res**: ModRuleData(id: int, type: str, value: Optional[str], action: str, duration_s: Optional[int], enabled: bool, exempt_roles: list[str])
- **Events emitted**: `moderation.action.requested` (action, channel_id, duration_s, message_id, platform, reason, rule_id, user), `moderation.action.taken` (action, channel_id, duration_s, message_id, platform, reason, rule_id, user), `moderation.rules.updated` (rule_id)
- **Events consumed**: chat.message.received, moderation.action.requested, moderation.rules.updated, viewer.regular.added, viewer.regular.removed
- **Dependencies**: ai, db, event_bus, http, logger, state, twitch, youtube
- **Plugins**: moderation.AiModPlugin, moderation.AutoModPlugin, moderation.CreateModRulePlugin, moderation.DeleteModRulePlugin, moderation.ListModRulesPlugin, moderation.ManualBanPlugin, moderation.ManualTimeoutPlugin, moderation.ManualUnbanPlugin, moderation.ModLogPlugin, moderation.ModerationActionRouterPlugin, moderation.UpdateModRulePlugin

### `overlays`
- **Table `overlay_feed_token`** (storage): id (int, PK), token (text, NOT NULL), created_at (timestamp, default CURRENT_TIMESTAMP), updated_at (timestamp, default CURRENT_TIMESTAMP)
- **Table `overlay_vars`** (storage): key (text, PK), value (text, NOT NULL), updated_at (timestamp, default CURRENT_TIMESTAMP)
- **Table `overlays`** (storage): id (int, PK), name (text, NOT NULL), config (text, NOT NULL, default '{"elements":[]}'), created_at (timestamp, default CURRENT_TIMESTAMP), updated_at (timestamp, default CURRENT_TIMESTAMP)
- **Model `OverlayEntity`** (domain vocabulary): id: int | None, name: str, config: str, created_at: datetime | None, updated_at: datetime | None
- **Endpoints**:
  - `DELETE /api/overlays/backgrounds/{filename}`
    - **res**: success: bool, error: Optional[str]
  - `DELETE /api/overlays/{id}`
    - **res**: success: bool, error: Optional[str]
  - `GET /api/overlays`
    - **res**: List[OverlayItem(id: int, name: str, created_at: Optional[str], updated_at: Optional[str])]
  - `GET /api/overlays/backgrounds`
    - **res**: list[BackgroundFileInfo(filename: str, url: str, type: str, size: int)]
  - `GET /api/overlays/data`
    - **res**: Any
  - `GET /api/overlays/manifest`
    - **res**: Any
  - `GET /api/overlays/token`
    - **res**: Any
  - `GET /api/overlays/{id}`
    - **res**: OverlayData(id: int, name: str, config: Any, created_at: Optional[str], updated_at: Optional[str])
  - `GET /api/overlays/{id}/config`
    - **res**: Any
  - `POST /api/overlays`
    - **req**: name: str, config: Optional[Any]
    - **res**: OverlayData(id: int, name: str, config: Any)
  - `POST /api/overlays/test`
    - **req**: type: str
    - **res**: Any
  - `POST /api/overlays/token`
    - **res**: Any
  - `POST /api/overlays/upload-background`
    - **res**: UploadBackgroundData(url: str, type: str)
  - `PUT /api/overlays/{id}`
    - **req**: name: Optional[str], config: Optional[Any]
    - **res**: OverlayData(id: int, name: str, config: Any, updated_at: Optional[str])
  - `SSE /api/overlays/feed`
  - `SSE /api/overlays/stream/{id}`
- **Events emitted**: `overlay.config.updated` (overlay_id), `overlay.test.event` (data, type)
- **Events consumed**: chat.message.received, dashboard.stats.updated, monetization.event.received, overlay.alert.trigger, overlay.config.updated, overlay.test.event, overlay.vars.set, youtube.superchat.received, youtube.supersticker.received
- **Dependencies**: db, event_bus, http, logger, state, twitch
- **Plugins**: overlays.CreateOverlayPlugin, overlays.DeleteBackgroundPlugin, overlays.DeleteOverlayPlugin, overlays.GetOverlayPlugin, overlays.ListBackgroundsPlugin, overlays.ListOverlaysPlugin, overlays.OverlayConfigPlugin, overlays.OverlayDataPlugin, overlays.OverlayFeedPlugin, overlays.OverlayManifestPlugin, overlays.OverlayStreamPlugin, overlays.OverlayTestPlugin, overlays.OverlayTokenPlugin, overlays.UpdateOverlayPlugin, overlays.UploadBackgroundPlugin

### `ping`
- **Tables**: none
- **Endpoints**:
  - `GET /api/ping`
    - **res**: PingData(status: str, message: str)
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: http, logger
- **Plugins**: ping.PingPlugin

### `platforms`
- **Table `platform_connections`** (storage): id (int, PK), platform (text, NOT NULL), channel_id (text, NOT NULL), channel_name (text, NOT NULL), enabled (int, NOT NULL, default 1), chat_read_enabled (int, NOT NULL, default 1), chat_write_enabled (int, NOT NULL, default 1), moderation_enabled (int, NOT NULL, default 0), capabilities (text, NOT NULL, default '{}'), created_at (text, default datetime('now')), updated_at (text, default datetime('now')) — UNIQUE(platform, channel_id)
- **Model `PlatformConnection`** (domain vocabulary): id: int, platform: str, channel_id: str, channel_name: str, enabled: bool, chat_read_enabled: bool, chat_write_enabled: bool, moderation_enabled: bool, capabilities: str, created_at: str, updated_at: str
- **Endpoints**:
  - `GET /api/platforms/connections`
    - **res**: list[PlatformConnectionData(id: int, platform: str, channel_id: str, channel_name: str, enabled: bool, chat_read_enabled: bool, chat_write_enabled: bool, moderation_enabled: bool, capabilities: dict, created_at: str, updated_at: str)]
  - `PUT /api/platforms/connections/{id}`
    - **req**: enabled: Optional[bool], chat_read_enabled: Optional[bool], chat_write_enabled: Optional[bool], moderation_enabled: Optional[bool], capabilities: Optional[dict]
    - **res**: PlatformConnectionData(id: int, platform: str, channel_id: str, channel_name: str, enabled: bool, chat_read_enabled: bool, chat_write_enabled: bool, moderation_enabled: bool, capabilities: dict, created_at: str, updated_at: str)
- **Events emitted**: `platform.connection.updated` ()
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger
- **Plugins**: platforms.ListPlatformConnectionsPlugin, platforms.UpdatePlatformConnectionPlugin

### `stream_outputs`
- **Table `stream_outputs`** (storage): id (int, PK), name (text, NOT NULL), platform (text, NOT NULL), channel_id (text, NOT NULL), enabled (int, NOT NULL, default 1), overlay_id (int), rtmp_url (text), stream_key_secret (text), status (text, NOT NULL, default 'stopped'), settings (text, NOT NULL, default '{}'), created_at (text, default datetime('now')), updated_at (text, default datetime('now'))
- **Model `StreamOutput`** (domain vocabulary): id: int, name: str, platform: str, channel_id: str, enabled: bool, overlay_id: Optional[int], rtmp_url: Optional[str], stream_key_configured: bool, stream_key_preview: Optional[str], status: str, settings: dict, created_at: str, updated_at: str
- **Endpoints**:
  - `DELETE /api/stream-outputs/{id}`
    - **res**: DeleteStreamOutputData(id: int, deleted: bool)
  - `GET /api/stream-outputs`
    - **res**: list[StreamOutputData(id: int, name: str, platform: str, channel_id: str, enabled: bool, overlay_id: Optional[int], rtmp_url: Optional[str], stream_key_configured: bool, stream_key_preview: Optional[str], status: str, settings: dict, created_at: str, updated_at: str)]
  - `GET /api/stream-outputs/runtime/status`
    - **res**: StreamRuntimeStatusData(input_url: str, obs_url: str, obs_stream_key: str, obs_connected: bool, ffmpeg_available: bool, rtmp_engine_available: bool, relays: dict, relays_count: int, live_outputs_count: int, enabled_outputs_count: Optional[int], is_transmitting: Optional[bool], active_source: str, fallback_running: bool, fallback_mode: str, fallback_video_configured: bool, fallback_video_path: Optional[str], fallback_video_url: Optional[str])
  - `POST /api/stream-outputs`
    - **req**: name: str, platform: str, channel_id: str, enabled: bool, overlay_id: Optional[int], rtmp_url: Optional[str], stream_key_secret: Optional[str], settings: dict
    - **res**: StreamOutputData(id: int, name: str, platform: str, channel_id: str, enabled: bool, overlay_id: Optional[int], rtmp_url: Optional[str], stream_key_configured: bool, stream_key_preview: Optional[str], status: str, settings: dict, created_at: str, updated_at: str)
  - `POST /api/stream-outputs/fallback/video`
    - **res**: UploadFallbackVideoData(mode: str, video_path: str, configured: bool)
  - `POST /api/stream-outputs/start-active`
    - **res**: list[StreamOutputData(id: int, name: str, platform: str, channel_id: str, enabled: bool, overlay_id: Optional[int], rtmp_url: Optional[str], stream_key_configured: bool, stream_key_preview: Optional[str], status: str, settings: dict, created_at: str, updated_at: str)]
  - `POST /api/stream-outputs/stop-active`
    - **res**: list[StreamOutputData(id: int, name: str, platform: str, channel_id: str, enabled: bool, overlay_id: Optional[int], rtmp_url: Optional[str], stream_key_configured: bool, stream_key_preview: Optional[str], status: str, settings: dict, created_at: str, updated_at: str)]
  - `POST /api/stream-outputs/{id}/start`
    - **res**: StreamOutputData(id: int, name: str, platform: str, channel_id: str, enabled: bool, overlay_id: Optional[int], rtmp_url: Optional[str], stream_key_configured: bool, stream_key_preview: Optional[str], status: str, settings: dict, created_at: str, updated_at: str)
  - `POST /api/stream-outputs/{id}/stop`
    - **res**: StreamOutputData(id: int, name: str, platform: str, channel_id: str, enabled: bool, overlay_id: Optional[int], rtmp_url: Optional[str], stream_key_configured: bool, stream_key_preview: Optional[str], status: str, settings: dict, created_at: str, updated_at: str)
  - `PUT /api/stream-outputs/{id}`
    - **req**: name: Optional[str], platform: Optional[str], channel_id: Optional[str], enabled: Optional[bool], overlay_id: Optional[int], rtmp_url: Optional[str], stream_key_secret: Optional[str], status: Optional[str], settings: Optional[dict]
    - **res**: StreamOutputData(id: int, name: str, platform: str, channel_id: str, enabled: bool, overlay_id: Optional[int], rtmp_url: Optional[str], stream_key_configured: bool, stream_key_preview: Optional[str], status: str, settings: dict, created_at: str, updated_at: str)
- **Events emitted**: none
- **Events consumed**: none
- **Dependencies**: db, http, logger, stream_tool
- **Plugins**: stream_outputs.CreateStreamOutputPlugin, stream_outputs.DeleteStreamOutputPlugin, stream_outputs.ListStreamOutputsPlugin, stream_outputs.StartActiveStreamOutputsPlugin, stream_outputs.StartStreamOutputPlugin, stream_outputs.StopActiveStreamOutputsPlugin, stream_outputs.StopStreamOutputPlugin, stream_outputs.StreamRuntimeStatusPlugin, stream_outputs.UpdateStreamOutputPlugin, stream_outputs.UploadFallbackVideoPlugin

### `stream_state`
- **Table `stream_sessions`** (storage): id (int, PK), twitch_stream_id (text), started_at (text, NOT NULL, default datetime('now')), ended_at (text), title (text), game_name (text), peak_viewers (int, NOT NULL, default 0)
- **Model `StreamSessionEntity`** (domain vocabulary): id: int, twitch_stream_id: Optional[str], started_at: str, ended_at: Optional[str], title: Optional[str], game_name: Optional[str], peak_viewers: int
- **Endpoints**:
  - `GET /api/stream/sessions`
    - **res**: list[StreamSessionData(id: int, twitch_stream_id: Optional[str], started_at: str, ended_at: Optional[str], title: Optional[str], game_name: Optional[str], peak_viewers: int)]
  - `GET /api/stream/status`
    - **res**: StreamStatusData(online: bool, session_id: Optional[int], started_at: Optional[str], ended_at: Optional[str], broadcaster_login: Optional[str])
- **Events emitted**: `stream.session.ended` (ended_at, session_id), `stream.session.started` (broadcaster_login, session_id, started_at, twitch_stream_id)
- **Events consumed**: stream.status.requested
- **Dependencies**: db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: stream_state.GetStreamStatusPlugin, stream_state.StreamHistoryPlugin, stream_state.StreamStateRpcPlugin, stream_state.StreamStatusPlugin

### `subscribers`
- **Table `gifters`** (storage): twitch_id (text, PK), login (text, NOT NULL), display_name (text, NOT NULL), gifts_total (int, NOT NULL, default 0), last_gift_at (text, NOT NULL, default datetime('now'))
- **Table `subscribers`** (storage): id (int, PK), twitch_id (text, NOT NULL), login (text, NOT NULL), display_name (text, NOT NULL), tier (text, NOT NULL, default '1000'), is_prime (int, NOT NULL, default 0), is_gift (int, NOT NULL, default 0), cumulative_months (int, NOT NULL, default 1), streak_months (int), subscribed_at (text, NOT NULL, default datetime('now')), last_sub_at (text, NOT NULL, default datetime('now')), is_active (int, NOT NULL, default 1) — UNIQUE(twitch_id)
- **Table `subscription_events`** (storage): id (int, PK), twitch_id (text, NOT NULL), login (text, NOT NULL), display_name (text, NOT NULL), event_type (text, NOT NULL), tier (text), previous_tier (text), cumulative_months (int), streak_months (int), is_gift (int, NOT NULL, default 0), gifter_id (text), gifter_login (text), gifter_display_name (text), event_at (text, NOT NULL, default datetime('now'))
- **Table `viewer_bits`** (storage): id (int, PK), twitch_id (text, NOT NULL), login (text, NOT NULL), display_name (text, NOT NULL), bits_total (int, NOT NULL, default 0), last_cheer_at (text, NOT NULL, default datetime('now')) — UNIQUE(twitch_id)
- **Model `Subscriber`** (domain vocabulary): id: int, twitch_id: str, login: str, display_name: str, tier: str, is_prime: bool, is_gift: bool, cumulative_months: int, streak_months: Optional[int], subscribed_at: str, last_sub_at: str, is_active: bool
- **Model `ViewerBits`** (domain vocabulary): id: int, twitch_id: str, login: str, display_name: str, bits_total: int, last_cheer_at: str
- **Endpoints**:
  - `GET /api/bits/leaderboard`
    - **res**: list[BitsEntry(rank: int, twitch_id: str, display_name: str, bits_total: int, last_cheer_at: str)]
  - `GET /api/gifters/leaderboard`
    - **res**: list[GifterEntry(rank: int, twitch_id: str, display_name: str, gifts_total: int, last_gift_at: str)]
  - `GET /api/subscribers/leaderboard`
    - **res**: list[SubscriberEntry(rank: int, twitch_id: str, display_name: str, tier: str, is_prime: bool, is_gift: bool, cumulative_months: int, streak_months: Optional[int], subscribed_at: str, is_active: bool)]], total: Optional[int
  - `POST /api/bits/sync`
    - **res**: SyncBitsData(synced: int)
  - `POST /api/subscribers/sync`
    - **res**: SyncSubscribersData(synced: int)
- **Events emitted**: `monetization.event.received` (amount_micros, channel_id, currency, display_amount, message, platform, raw, timestamp, type, user), `subscriber.expired` (twitch_id), `subscriber.gift` (cumulative_total, gifter_id, gifter_name, total), `subscriber.new` (display_name, is_gift, tier, twitch_id), `subscriber.resub` (cumulative_months, display_name, streak_months, tier, twitch_id), `viewer.bits.received` (bits, display_name, twitch_id)
- **Events consumed**: none
- **Dependencies**: db, event_bus, http, logger, twitch
- **Plugins**: subscribers.BitsLeaderboardPlugin, subscribers.BitsTrackerPlugin, subscribers.GiftersLeaderboardPlugin, subscribers.SubscribersLeaderboardPlugin, subscribers.SubscriptionTrackerPlugin, subscribers.SyncBitsPlugin, subscribers.SyncSubscribersPlugin

### `system`
- **Table `scheduler_one_shots`** (storage): job_id (text, PK), run_at_epoch (float, NOT NULL), event (text, NOT NULL), payload (text, NOT NULL)
- **Model `SchedulerOneShotEntity`** (domain vocabulary): job_id: str, run_at_epoch: float, event: str, payload: str
- **Endpoints**:
  - `GET /api/system/events`
    - **res**: SystemEventsData(events: list[EventEntry(event: str, subscribers: list[str], last_emitters: list[str], times_fired: int)])
  - `GET /api/system/lint`
    - **res**: SystemLintData(arch_violations: list[str], drift_warnings: list[str], event_contract_violations: list[LintFinding(code: str, severity: str, event: Optional[str], publisher: Optional[str], consumer: Optional[str], detail: str)])
  - `GET /api/system/metrics`
    - **res**: list[MetricRecord(tool: str, method: str, duration_ms: float, success: bool, timestamp: float)]
  - `GET /api/system/status`
    - **res**: SystemStatusData(tools: list[ToolStatus(name: str, status: str, message: Optional[str])], plugins: list[PluginStatus(name: str, domain: Optional[str], status: str, error: Optional[str], tools: list[str])])
  - `GET /api/system/traces/flat`
    - **res**: list[TraceFlatNode(id: str, parent_id: Optional[str], event: str, emitter: str, subscribers: list[str], payload_keys: list[str], timestamp: float, key: Optional[str], priority: Optional[int], delay: Optional[int])]
  - `GET /api/system/traces/tree`
    - **res**: list[TraceNode(id: str, parent_id: Optional[str], event: str, emitter: str, subscribers: list[str], payload_keys: list[str], timestamp: float, key: Optional[str], priority: Optional[int], delay: Optional[int], children: list['TraceNode'])]
  - `SSE /api/system/events/stream`
  - `SSE /api/system/logs/stream`
  - `SSE /api/system/metrics/stream`
  - `SSE /api/system/traces/stream`
- **Events emitted**: `event.delivery.failed` ()
- **Events consumed**: system.one_shot.cancel, system.one_shot.schedule
- **Dependencies**: config, container, db, event_bus, http, logger, registry, scheduler
- **Plugins**: system.ArchitectureLinterPlugin, system.DurableOneShotsPlugin, system.EventContractLinterPlugin, system.EventDeliveryMonitorPlugin, system.SystemEventsPlugin, system.SystemEventsStreamPlugin, system.SystemLogsStreamPlugin, system.SystemMetricsPlugin, system.SystemStatusPlugin, system.SystemTracesPlugin, system.SystemTracesStreamPlugin, system.ToolHealthPlugin

### `timers`
- **Table `timers`** (storage): id (int, PK), name (text, NOT NULL), message (text, NOT NULL), interval_minutes (int, NOT NULL), min_lines (int, NOT NULL, default 0), enabled (int, default 1), last_executed_at (timestamp), created_at (timestamp, default CURRENT_TIMESTAMP)
- **Model `TimerEntity`** (domain vocabulary): id: int | None, name: str, message: str, interval_minutes: int, min_lines: int, enabled: int, last_executed_at: datetime | None, created_at: datetime | None
- **Endpoints**:
  - `DELETE /api/timers/{id}`
    - **res**: success: bool, error: Optional[str]
  - `GET /api/timers`
    - **res**: List[TimerData(id: int, name: str, message: str, interval_minutes: int, min_lines: int, enabled: int, last_executed_at: Optional[str])]
  - `POST /api/timers`
    - **req**: name: str, message: str, interval_minutes: int, min_lines: int, enabled: int
    - **res**: TimerData(id: int, name: str, message: str, interval_minutes: int, min_lines: int, enabled: int)
  - `PUT /api/timers/{id}`
    - **req**: name: Optional[str], message: Optional[str], interval_minutes: Optional[int], min_lines: Optional[int], enabled: Optional[int]
    - **res**: TimerData(id: int, name: str, message: str, interval_minutes: int, min_lines: int, enabled: int)
- **Events emitted**: `chat.message.send` (channel_id, channel_name, message, platform), `timer.created` (id, name), `timer.deleted` (id), `timer.updated` (id)
- **Events consumed**: chat.message.received, timer.created, timer.deleted, timer.updated
- **Dependencies**: db, event_bus, http, logger, scheduler, state, twitch
- **Plugins**: timers.CreateTimerPlugin, timers.DeleteTimerPlugin, timers.GetTimersPlugin, timers.TimerExecutorPlugin, timers.UpdateTimerPlugin

### `tts_chat`
- **Table `tts_settings`** (storage): id (int, PK, default 1), enabled (int, NOT NULL, default 1), default_voice (text, NOT NULL, default 'es-ES-AlvaroNeural'), max_message_length (int, NOT NULL, default 200), skip_commands (int, NOT NULL, default 1), skip_links (int, NOT NULL, default 1), sub_only (int, NOT NULL, default 0), cooldown_seconds (int, NOT NULL, default 0), blocked_words (text, NOT NULL, default '[]'), updated_at (text, NOT NULL, default datetime('now')), redemption_title (text, NOT NULL, default ''), mod_bypass (int, NOT NULL, default 1)
- **Table `tts_user_voice`** (storage): id (int, PK), twitch_id (text, NOT NULL), twitch_login (text, NOT NULL), voice_id (text, NOT NULL), voice_name (text, NOT NULL), created_at (text, NOT NULL, default datetime('now')), updated_at (text, NOT NULL, default datetime('now')) — UNIQUE(twitch_id)
- **Model `TtsUserVoiceEntity`** (domain vocabulary): id: int, twitch_id: str, twitch_login: str, voice_id: str, voice_name: str, provider: str, created_at: str, updated_at: str
- **Model `TtsSettingsEntity`** (domain vocabulary): id: int, enabled: bool, provider: str, host: str, port: int, default_voice: str, timeout_s: int, max_message_length: int, skip_commands: bool, skip_links: bool, sub_only: bool, cooldown_seconds: int, blocked_words: str, updated_at: str
- **Endpoints**:
  - `DELETE /api/tts/user-voices/{twitch_login}`
    - **res**: success: bool, error: Optional[str]
  - `GET /api/tts/settings`
    - **res**: TtsSettingsData(enabled: bool, default_voice: str, max_message_length: int, skip_commands: bool, skip_links: bool, sub_only: bool, mod_bypass: bool, cooldown_seconds: int, blocked_words: list[str], redemption_title: str, providers: dict[str, bool], updated_at: str)
  - `GET /api/tts/user-voices`
    - **res**: list[UserVoiceItem(id: int, twitch_id: str, twitch_login: str, voice_id: str, voice_name: str, updated_at: str)]
  - `GET /api/tts/user-voices/{twitch_login}`
    - **res**: UserVoiceItem(id: int, twitch_id: str, twitch_login: str, voice_id: str, voice_name: str, updated_at: str)
  - `GET /api/tts/voices`
    - **res**: list[VoiceItem(id: str, name: str, gender: str, locale: str, provider: str)]
  - `PUT /api/tts/settings`
    - **req**: enabled: Optional[bool], default_voice: Optional[str], max_message_length: Optional[int], skip_commands: Optional[bool], skip_links: Optional[bool], sub_only: Optional[bool], mod_bypass: Optional[bool], cooldown_seconds: Optional[int], blocked_words: Optional[list[str]], redemption_title: Optional[str]
    - **res**: TtsSettingsData(enabled: bool, default_voice: str, max_message_length: int, skip_commands: bool, skip_links: bool, sub_only: bool, mod_bypass: bool, cooldown_seconds: int, blocked_words: list[str], redemption_title: str, providers: dict[str, bool], updated_at: str)
  - `PUT /api/tts/user-voices`
    - **req**: twitch_id: str, twitch_login: str, voice_id: str, voice_name: str
    - **res**: UserVoiceItem(id: int, twitch_id: str, twitch_login: str, voice_id: str, voice_name: str, updated_at: str)
  - `SSE /api/tts/overlay/stream`
- **Events emitted**: `chat.message.send` (channel_id, channel_name, message, platform), `tts.audio.ready` (audio_b64, text, username, voice_id)
- **Events consumed**: chat.message.received, tts.audio.ready
- **Dependencies**: db, event_bus, http, logger, tts, twitch
- **Plugins**: tts_chat.TtsListenerPlugin, tts_chat.TtsRedemptionPlugin, tts_chat.TtsRestoreConfigPlugin, tts_chat.TtsSettingsPlugin, tts_chat.TtsStreamPlugin, tts_chat.TtsUserVoicesPlugin, tts_chat.TtsVoiceCommandPlugin, tts_chat.TtsVoiceListPlugin

### `twitch_auth`
- **Table `twitch_tokens`** (storage): id (int, PK), twitch_id (text, NOT NULL), login (text, NOT NULL), display_name (text, NOT NULL), access_token (text, NOT NULL), refresh_token (text, NOT NULL), scopes (text, NOT NULL, default '[]'), expires_at (text, NOT NULL), created_at (text, NOT NULL, default datetime('now')), updated_at (text, NOT NULL, default datetime('now')) — UNIQUE(twitch_id)
- **Model `TwitchTokenEntity`** (domain vocabulary): id: int, twitch_id: str, login: str, display_name: str, access_token: str, refresh_token: str, scopes: str, expires_at: str, created_at: str, updated_at: str
- **Endpoints**:
  - `GET /api/auth/twitch`
    - **res**: dict
  - `GET /api/auth/twitch/callback`
    - **res**: dict
  - `GET /api/auth/twitch/scopes`
    - **res**: ScopesData(connected: bool, required: Optional[list[str]], granted: Optional[list[str]], missing: Optional[list[str]])
  - `GET /api/auth/twitch/status`
    - **res**: AuthStatusData(authenticated: bool, connected: bool, connecting: bool, login: Optional[str], broadcaster_id: Optional[str])
  - `POST /api/auth/twitch/logout`
    - **res**: dict
- **Events emitted**: `platform.connection.updated` (capabilities, channel_id, channel_name, chat_read_enabled, chat_write_enabled, enabled, id, moderation_enabled, platform)
- **Events consumed**: none
- **Dependencies**: config, db, event_bus, http, logger, scheduler, twitch
- **Plugins**: twitch_auth.RestoreSessionPlugin, twitch_auth.TwitchAuthStatusPlugin, twitch_auth.TwitchEventBridgePlugin, twitch_auth.TwitchLogoutPlugin, twitch_auth.TwitchOAuthCallbackPlugin, twitch_auth.TwitchOAuthStartPlugin, twitch_auth.TwitchScopesPlugin, twitch_auth.TwitchTokenRefreshPlugin

### `viewers`
- **Table `viewers`** (storage): id (int, PK), global_user_id (text, NOT NULL), platform (text, NOT NULL), platform_user_id (text, NOT NULL), login (text), display_name (text, NOT NULL), avatar_url (text), points (int, NOT NULL, default 0), total_earned (int, NOT NULL, default 0), is_regular (int, NOT NULL, default 0), first_seen (text, default datetime('now')), last_seen (text, default datetime('now')) — UNIQUE(global_user_id) — UNIQUE(platform, platform_user_id)
- **Model `Viewer`** (domain vocabulary): id: int, global_user_id: str, platform: str, platform_user_id: str, login: Optional[str], display_name: str, avatar_url: Optional[str], points: int, total_earned: int, is_regular: bool, first_seen: str, last_seen: str
- **Endpoints**:
  - `DELETE /api/viewers/regulars/{global_user_id}`
    - **res**: success: bool, error: Optional[str]
  - `GET /api/viewers`
    - **res**: List[ViewerData(id: int, global_user_id: str, platform: str, platform_user_id: str, login: Optional[str], display_name: str, avatar_url: Optional[str], points: int, total_earned: int, is_regular: bool, first_seen: str, last_seen: str)]
  - `GET /api/viewers/leaderboard`
    - **res**: list[LeaderboardEntry(rank: int, global_user_id: str, platform: str, platform_user_id: str, display_name: str, points: int, total_earned: int, is_regular: bool)]
  - `GET /api/viewers/regulars`
    - **res**: list[RegularEntry(global_user_id: str, platform: str, platform_user_id: str, login: Optional[str], display_name: str, points: int, first_seen: str)]
  - `GET /api/viewers/{query}`
    - **res**: ViewerData(id: int, global_user_id: str, platform: str, platform_user_id: str, login: Optional[str], display_name: str, avatar_url: Optional[str], points: int, total_earned: int, is_regular: bool, first_seen: str, last_seen: str)
  - `POST /api/viewers/regulars`
    - **req**: login: str, platform: str
    - **res**: RegularData(global_user_id: str, platform: str, platform_user_id: str, login: Optional[str], display_name: str)
  - `POST /api/viewers/{global_user_id}/points`
    - **req**: delta: int
    - **res**: ViewerData(global_user_id: str, platform: str, platform_user_id: str, display_name: str, points: int, total_earned: int)
- **Events emitted**: `chat.message.send` (message), `viewer.points.awarded` (delta, display_name, global_user_id, platform, platform_user_id), `viewer.regular.added` (added_by, display_name, global_user_id, platform, platform_user_id), `viewer.regular.removed` ()
- **Events consumed**: chat.command.received, chat.message.received
- **Dependencies**: db, event_bus, http, logger, twitch
- **Plugins**: viewers.AddRegularPlugin, viewers.AdjustPointsPlugin, viewers.GetViewerPlugin, viewers.LeaderboardPlugin, viewers.ListRegularsPlugin, viewers.ListViewersPlugin, viewers.RegularsCommandPlugin, viewers.RemoveRegularPlugin, viewers.ViewerActivityPlugin

### `webhooks`
- **Table `webhooks`** (storage): id (int, PK), name (text, NOT NULL), url (text, NOT NULL), method (text, NOT NULL, default 'POST'), headers (text), body_template (text), trigger_type (text, NOT NULL), trigger_value (text, NOT NULL), enabled (int, NOT NULL, default 1), created_at (text, NOT NULL, default datetime('now')), updated_at (text, NOT NULL, default datetime('now')), filter_field (text), filter_value (text)
- **Model `WebhookEntity`** (domain vocabulary): id: Optional[int], name: str, url: str, method: str, headers: Optional[str], body_template: Optional[str], trigger_type: str, trigger_value: str, filter_field: Optional[str], filter_value: Optional[str], enabled: bool, created_at: Optional[str], updated_at: Optional[str]
- **Endpoints**:
  - `DELETE /api/webhooks/{webhook_id}`
    - **res**: success: bool, error: Optional[str]
  - `GET /api/webhooks`
    - **res**: List[WebhookEntry(id: int, name: str, url: str, method: str, trigger_type: str, trigger_value: str, filter_field: Optional[str], filter_value: Optional[str], body_template: Optional[str], enabled: bool)]
  - `POST /api/webhooks`
    - **req**: name: str, url: str, method: str, headers: Optional[str], body_template: Optional[str], trigger_type: str, trigger_value: str, filter_field: Optional[str], filter_value: Optional[str], enabled: bool
    - **res**: WebhookData(id: int, name: str, url: str, trigger_type: str, trigger_value: str, enabled: bool)
  - `POST /api/webhooks/test`
    - **req**: url: str, method: str, headers: Optional[str], body_template: Optional[str]
    - **res**: success: bool, status: Optional[int], response: Optional[str], error: Optional[str]
  - `PUT /api/webhooks/{webhook_id}`
    - **res**: success: bool, error: Optional[str]
- **Events emitted**: none
- **Events consumed**: chat.command.executed
- **Dependencies**: db, event_bus, http, http_client, logger
- **Plugins**: webhooks.CreateWebhookPlugin, webhooks.DeleteWebhookPlugin, webhooks.ListWebhooksPlugin, webhooks.TestWebhookPlugin, webhooks.UpdateWebhookPlugin, webhooks.WebhookExecutorPlugin

### `youtube_auth`
- **Table `youtube_tokens`** (storage): id (int, PK), channel_id (text, NOT NULL), channel_title (text, NOT NULL), access_token (text, NOT NULL), refresh_token (text), scopes (text, NOT NULL, default '[]'), expires_at (text, NOT NULL), created_at (text, default datetime('now')), updated_at (text, default datetime('now')) — UNIQUE(channel_id)
- **Model `YouTubeToken`** (domain vocabulary): id: Optional[int], channel_id: str, channel_title: str, access_token: str, refresh_token: Optional[str], scopes: str, expires_at: str, created_at: Optional[str], updated_at: Optional[str]
- **Endpoints**:
  - `GET /api/auth/youtube`
    - **res**: dict
  - `GET /api/auth/youtube/callback`
    - **res**: dict
  - `GET /api/auth/youtube/status`
    - **res**: YouTubeAuthStatusData(authenticated: bool, connected: bool, channel_id: Optional[str], channel_title: Optional[str])
  - `POST /api/auth/youtube/logout`
    - **res**: dict
- **Events emitted**: `platform.connection.updated` (capabilities, channel_id, channel_name, chat_read_enabled, chat_write_enabled, enabled, id, moderation_enabled, platform)
- **Events consumed**: none
- **Dependencies**: config, db, event_bus, http, logger, youtube
- **Plugins**: youtube_auth.RestoreYouTubeSessionPlugin, youtube_auth.YouTubeAuthStatusPlugin, youtube_auth.YouTubeLogoutPlugin, youtube_auth.YouTubeOAuthCallbackPlugin, youtube_auth.YouTubeOAuthStartPlugin, youtube_auth.YouTubeTokenRefreshPlugin

### `youtube_chat`
- **Tables**: none
- **Endpoints**: none
- **Events emitted**: `chat.command.received` (args, command), `chat.message.deleted` (channel_id, message_id, platform, raw, timestamp), `chat.message.received` (), `monetization.event.received` (amount_micros, currency, display_amount, type)
- **Events consumed**: none
- **Dependencies**: db, event_bus, logger, youtube
- **Plugins**: youtube_chat.YouTubeChatPollerPlugin

## 🧩 Plugin Authoring Guide

> Embedded verbatim from `tools/context/authoring_guide.md` on every boot.
> For a wave executor this section IS the rulebook: the canonical executor
> prompt is this manifest → `plans/active_plan.yaml` → ONE task line at the
> end — nothing else.

### Executor contract — exactly two files

Your only job is the ONE feature named in the final line of your prompt.
Everything you need is in this manifest and the plan (your contract). Do not
read any other file. Do not ask questions — the plan already made every
decision.

Your task line names either a **feature** or a **flow's tests**:

- Feature task → 1. the plugin (at the `file:` path your feature declares)
  and 2. its test (at the `test:` path it declares).
- Flow-tests task → 1. the flow's `e2e_test` (trigger the happy path, assert
  the causal chain with `tests/helpers/trace_chains.py`:
  `assert_chain(build_tree(bus.get_trace_history()), [...])`) and 2. its
  `sad_path_test` (force the consumer to fail — the mock must raise on the
  FIRST tool call the handler makes, since an idempotency guard runs before
  the effect — and assert `_dlq.<event>` appears as a child of the failed
  event in the same tree).

Nothing else: no migrations, no entity models, no edits to `main.py`, no
touching other domains or other tasks' files. When both files are written,
stop. Do not run commands, do not summarize the codebase, do not propose
follow-ups.

### Plugin rules

1. **Schemas inline** — request, response AND event payload models at the top
   of the plugin file. Never import them from `models/` or other domains.
2. **DI by parameter name** — `__init__(self, http, db, logger)` receives the
   tools named `http`, `db`, `logger`. No hardcoded imports from `tools/`.
   **Your parameters are exactly your feature's `tools:` list in the plan, in
   that order** — not what a template happens to show. The plan is the
   contract, and the test is written against that same list: add a `logger`
   nobody declared and the two no longer fit.
3. **Return envelope** — `{"success": bool, "data": ..., "error": ...}`:
   `success` always present, `data` on success, `error` on failure. Responses
   serialize AS-IS — `response_model` does NOT backfill omitted keys, so an
   omitted key is simply absent from the JSON. All values in `data` must be
   JSON-serializable (`.model_dump()` Pydantic instances before returning).
4. **SQL placeholders** — `$1, $2, $3...` (PostgreSQL style), only on tables
   your feature's `db:` contract declares.
5. **Events** — publish exactly the events the plan declares, with a
   `XxxPayload(BaseModel)` defined in THIS file and published via
   `XxxPayload(...).model_dump()` (bare call, no arguments). Consumers never
   import the publisher's model: declare your own model with only the fields
   your `consumes.requires` lists (tolerant reader) and do `Model(**event.payload)`.
6. **Subscribers receive the event envelope** — access data via
   `event.payload`. Leave the parameter untyped (no annotation, no import),
   exactly as the subscriber template below shows.
7. **Safe errors** — never return `str(e)` to the client. Log it, return a
   generic message ("Database error").
8. **Protected route?** If the plan marks it, pass
   `auth_validator=self.auth.validate_token` to `add_endpoint` and check
   ownership via `data["_auth"]["sub"]`.
9. **Always pass `response_model=`** to `add_endpoint`, and `Field(...)`
   constraints on every request field.
10. **Field names are not yours to invent.** Anything backed by a table column
    carries that column's EXACT name — the `Table` lines above are the source
    (they are read from the live database, so they are the real names). Never
    rename in transit: `email` stays `email`, `user_id` stays `user_id`.
    For the fields nothing else pins, use these spellings and no synonyms:

    | Purpose | Use | Never |
    |---|---|---|
    | pagination | `limit`, `offset` | `page`, `per_page`, `take`, `skip` |
    | free-text search | `q` | `query`, `search`, `term` |
    | list totals (inside `data`) | `total`, `has_more` | `total_count`, `count`, `next_cursor` |
    | sorting | `sort_by`, `order` (`asc`/`desc`) | `sort`, `orderBy`, `direction` |

    Every executor in a wave reads this same table, which is what keeps the API
    coherent without any of you coordinating: your feature is written in
    isolation, but its vocabulary is shared.

11. **Idempotent consumer** — when the plan's flow link says
    `idempotent: true`, guard on `event.id` (the envelope is frozen, so a
    redelivery carries the same one) and mark it **after** the effect:

    ```python
    if await self.state.has(event.id, namespace="thing-seen"):
        return                                    # duplicate: already applied
    await self.state.increment(...)               # the effect
    await self.state.set(event.id, True, namespace="thing-seen", ttl=3600)
    ```

    Marking first drops the event: the effect raises, the retry hits the guard,
    returns "already seen" — no work, no error, no DLQ. Write the
    double-delivery test under the exact name `idempotency_test` gives, with a
    real `StateTool()`: an `AsyncMock` returns truthy from `has()`, so the guard
    swallows everything and the test proves nothing. Rule 6 says a *plugin*
    never imports the envelope; a *test* has to build one —
    `from tools.event_bus.envelope import EventEnvelope` — and deliver the same
    instance twice.

### Templates — one per deliverable type, copy the one your task matches

Each is a whole file, imports to last line; nothing a feature or flow-tests
task needs is missing from them.

#### Publisher feature (endpoint + event)

```python
from typing import Optional
from pydantic import BaseModel, Field
from microcoreos import BasePlugin

class CreateThingRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)

class ThingData(BaseModel):
    id: int
    name: str

class CreateThingResponse(BaseModel):
    success: bool
    data: Optional[ThingData] = None
    error: Optional[str] = None

class ThingCreatedPayload(BaseModel):
    id: int
    name: str

class CreateThingPlugin(BasePlugin):
    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint("/things", "POST", self.execute,
                               tags=["Things"], request_model=CreateThingRequest,
                               response_model=CreateThingResponse)

    async def execute(self, data: dict, context=None):
        try:
            req = CreateThingRequest(**data)
            new_id = await self.db.execute(
                "INSERT INTO things (name) VALUES ($1) RETURNING id", [req.name]
            )
            await self.bus.publish(
                "thing.created", ThingCreatedPayload(id=new_id, name=req.name).model_dump()
            )
            return {"success": True, "data": {"id": new_id, "name": req.name}}
        except Exception as e:
            self.logger.error(f"Failed to create thing: {e}")
            return {"success": False, "error": "Database error"}
```

#### Subscriber feature (pure event consumer)

```python
from pydantic import BaseModel
from microcoreos import BasePlugin


# Consumed event, tolerant reader: declare ONLY the fields your feature's
# `consumes.requires` lists — never import the publisher's model.
class ThingCreatedData(BaseModel):
    id: int
    name: str


class ThingAuditedPayload(BaseModel):
    thing_id: int


class ThingAuditPlugin(BasePlugin):
    def __init__(self, event_bus, logger):
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        # retries/backoff: exactly what the plan's flow link declares (omit when none)
        await self.bus.subscribe("thing.created", self.on_thing_created)

    async def on_thing_created(self, event) -> None:
        data = ThingCreatedData(**event.payload)
        self.logger.info(f"Audited thing {data.id}")
        await self.bus.publish(
            "thing.audited", ThingAuditedPayload(thing_id=data.id).model_dump()
        )
```

#### Flow tests (e2e chain + sad path — one file with both)

```python
"""Flow tests for <flow-name>: happy-path causal chain + DLQ sad path."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from domains.things.plugins.create_thing_plugin import CreateThingPlugin
from domains.things.plugins.thing_audit_plugin import ThingAuditPlugin
from tools.event_bus.event_bus_tool import EventBusTool
from tests.helpers.async_wait import wait_for_dlq, wait_until
from tests.helpers.trace_chains import build_tree, assert_chain

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bus():
    b = EventBusTool()
    await b.setup()
    yield b
    await b.shutdown()


async def test_happy_path_chain(bus):
    db = AsyncMock()
    db.execute.return_value = 1

    publisher = CreateThingPlugin(http=MagicMock(), db=db, event_bus=bus, logger=MagicMock())
    consumer = ThingAuditPlugin(event_bus=bus, logger=MagicMock())
    await consumer.on_boot()

    await publisher.execute({"name": "widget"})
    await wait_until(lambda: any(r.envelope.event == "thing.audited"
                                 for r in bus.get_trace_history()))

    assert_chain(build_tree(bus.get_trace_history()),
                 ["thing.created", "thing.audited"])


async def test_sad_path_dlq(bus):
    logger = MagicMock()
    consumer = ThingAuditPlugin(event_bus=bus, logger=logger)
    await consumer.on_boot()

    # Force the consumer to fail on every attempt: one injected tool raises.
    logger.info.side_effect = RuntimeError("forced failure")

    await bus.publish("thing.created", {"id": 1, "name": "widget"})
    # DLQ fires only after retries exhaust their exponential backoff, which
    # can exceed wait_until's default timeout. wait_for_dlq derives its
    # deadline from the retries/backoff the subscribers declared — always
    # use it for DLQs, never plain wait_until, never a hand-picked timeout.
    await wait_for_dlq(bus, "thing.created")

    # _dlq.<event> is published inside the failing delivery's context, so it
    # appears as a child of the event that failed — same helper asserts it.
    assert_chain(build_tree(bus.get_trace_history()),
                 ["thing.created", "_dlq.thing.created"])
```

#### Mocking tool methods used as `async with` (async context managers)

Rule: on a mocked tool, any method the plugin uses as
`async with tool.method() as x:` must be overridden with `MagicMock`.
A bare `AsyncMock()` breaks there — every method call on an `AsyncMock`
returns a coroutine, which has no `__aenter__`/`__aexit__`, so the test
crashes with `TypeError: 'coroutine' object does not support the
asynchronous context manager protocol`. These methods are *sync* calls that
return an async context manager; `MagicMock` handles them because it
auto-configures `__aenter__`/`__aexit__` as `AsyncMock`.

For `db.transaction()` never build the nested mock by hand — use the
prefabricated helpers from `tests.helpers.mock_db`:

```python
from tests.helpers.mock_db import TxMock, FailingTxMock

db = AsyncMock()

# Happy path: stub and assert on the tx instance itself.
tx = TxMock()
tx.execute.return_value = 1
db.transaction = MagicMock(return_value=tx)
# ... exercise the plugin ...
tx.execute.assert_awaited_with("INSERT INTO ...", [...])

# Sad path (rollback / DLQ): every tx operation raises RuntimeError.
db.transaction = MagicMock(return_value=FailingTxMock())
```

For any *other* tool method entered with `async with` (no prefab helper),
override it with a bare `MagicMock` and remember the object bound by
`as x:` is `__aenter__`'s return value — NOT `method.return_value`:

```python
tool.method = MagicMock()  # sync call returning an async context manager
x = tool.method.return_value.__aenter__.return_value  # bound by `as x:`
```

### Test rules

- **Write the test FIRST, then the plugin.** Derive every assertion from the
  PLAN (route, envelope shape, declared tables, declared payload keys) —
  never from your own implementation. The test is the contract's proof; a
  test that mirrors the code proves nothing.
- Mock exactly the tools your feature's `tools:` lists — all of them; an
  omitted `logger` is still a required positional argument
  (`unittest.mock.AsyncMock` / `MagicMock`); run every other injected tool as
  a real in-memory instance (SQLite `:memory:` with your domain's migration
  applied, in-process event bus).
- On a mocked tool, every method the plugin uses as `async with` must be
  overridden with `MagicMock` — a bare `AsyncMock` crashes there. For
  `db.transaction()` always use `TxMock` / `FailingTxMock` from
  `tests.helpers.mock_db`; for other tools see "Mocking tool methods used as
  `async with`" above.
- Always wait for DLQ events with `wait_for_dlq` from
  `tests.helpers.async_wait`, never with plain `wait_until`: the DLQ only
  fires after retries exhaust their exponential backoff, which can exceed
  `wait_until`'s default timeout. Do not pass a timeout — the helper derives
  the deadline from the retries/backoff the subscribers declared.
- Prove the black-box contract: input → output envelope, DB effects on the
  declared tables, published payloads with the declared keys. Assert the keys
  the envelope guarantees (`success`, plus `data` on success / `error` on
  failure); the complementary key may be legitimately absent — use `.get()`
  for it, never a bare `result["key"]`.
- One error-path test: force a failure (mock that raises) and assert the
  technical detail does NOT reach the client response.
- Mark async tests with `@pytest.mark.anyio` (add an `anyio_backend`
  fixture returning `"asyncio"`).
- **Never a fixed `asyncio.sleep()` to wait for async delivery** — it guesses
  a duration and flakes under CI CPU contention. Poll the real condition with
  `wait_until` from `tests.helpers.async_wait` (as in the flow-test template
  above). The one exception is a negative check (asserting nothing arrives),
  where a short fixed sleep is the only option.
