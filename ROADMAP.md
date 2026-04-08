# Roadmap

---

## High Priority

**Issue 1 — ✅ Domain-level AI_CONTEXT.md**

Each domain should have an auto-generated or manual `AI_CONTEXT.md` that gives the AI all the context it needs in 10 lines:

```
# Domain: profiles
## Tables: profiles, links
## Endpoints: POST/GET/PUT/DELETE /profiles, POST /profiles/{id}/links
## Events emitted: profile.created, profile.updated
## Events consumed: none
## Dependencies: db, http, logger, auth, event_bus
```

This eliminates the need to read code to understand a domain. The AI reads this file first and knows exactly what exists, what it can use, and what events flow in and out.

---

## Medium Priority

**Issue 11 — ✅ Normalize 422 validation errors to standard envelope**

FastAPI's default 422 response uses its own format instead of `{"success": bool, "error": ..., "details": ...}`.
Fixed by registering a `RequestValidationError` exception handler in `HttpServerTool.setup()`.
The handler returns the first validation message as `error` and the full Pydantic error list as `details`.
All HTTP responses are now guaranteed to use the same envelope regardless of error type.

---

**Issue 2 — ✅ Standardized validation pattern**

`pydantic.Field` with explicit constraints is the only accepted pattern for request schemas.
Documented in `INSTRUCTIONS_FOR_AI.md` under the "Validation standard" section.

---

**Issue 5 — Event contract validation (deferred — needs dedicated linter)**

A plugin can publish `user.created` with `{id, email}` while the subscriber expects `{user_id, email}` — silent failure at runtime.

Deferred because adding schema validation inside the bus or the core would introduce coupling that goes against the decoupled-by-design philosophy. The right approach is a static linter that runs outside the runtime — a separate tool that cross-references `publish()` call signatures vs subscriber expectations from source code, with zero runtime overhead.

Tackle when a linting/CI layer is added to the project.

---

**Issue 12 — Scheduler tool**

A background job tool that lets plugins register cron-style or one-off tasks without polling loops.

Proposed design:
- `tools/scheduler/scheduler_tool.py` — name = `"scheduler"`
- Backend-agnostic: default impl wraps APScheduler (zero infra required); swappable to Celery beat via the same replacement standard (same `name`, same API, plugins unaffected)
- Public API: `add_job(cron_expr, callback)`, `add_one_shot(run_at, callback)`, `remove_job(job_id)`
- Follows the same swap pattern as `db`: change the tool file, nothing else changes

Deferred: no immediate use case in the current domain set. Tackle when the first background job requirement appears.

---

**Issue 13 — Rate limiter tool**

A stateless rate limiting tool backed by Redis (sliding window algorithm). In-memory fallback for single-process deploys.

Proposed design:
- `tools/rate_limiter/rate_limiter_tool.py` — name = `"rate_limiter"`
- Public API: `check(key: str, limit: int, window_seconds: int) -> bool`
- Plugins decide which endpoints call it and what the limits are — rate limiting policy is business logic, the tool is infrastructure
- Requires Redis tool to be available for distributed deployments; degrades to in-memory for single-process

Deferred: depends on Redis tool being added first. Tackle together.

---

## Optional / Exploratory

**Issue 10 — Migrate HttpServerTool from FastAPI to pure Starlette**

FastAPI is already a thin wrapper over Starlette. Most imports in `http_server_tool.py` are Starlette classes re-exported by FastAPI (`Request`, `WebSocket`, `JSONResponse`, `StreamingResponse`, `StaticFiles`, `CORSMiddleware`, `run_in_threadpool`). What is exclusively FastAPI is minimal: the app object, `Depends()` for GET query params, `response_model`/`tags` in `add_api_route`, and the auto-generated docs at `/docs`.

**Motivation:** simplify the tool by removing the `__signature__` hack in `_register_endpoint` (which exists solely to make FastAPI generate correct OpenAPI docs), and align the framework with its long-term identity.

**Trade-off:** auto-generated docs at `/docs` are lost.

**Mitigation — Issue 10b — Optional `OpenApiPlugin`:**

A plugin that lazily generates the OpenAPI 3.0 spec on the first request to `/openapi.json` and serves Swagger UI at `/docs` pointing to that endpoint. `HttpServerTool` exposes `get_registered_routes()` with the metadata accumulated in `_pending_endpoints`. The plugin is fully optional — if absent, no docs are served. Can be excluded in production without any other changes.

Follows the 1 file = 1 feature principle.

---

## Low Priority

**Issue 6 — ✅ Proactive tool health check**

`ToolProxy` detects failures reactively (when an operation raises an exception). Tools that fail silently (e.g. DB stops responding without raising) remain in `OK` state indefinitely.

Solution: a background plugin that calls `db.health_check()` every N seconds and updates the registry. The DB tool already exposes `health_check() -> bool`. The pattern is extensible to any tool that implements the method.

Note: keep outside the core to preserve the blind-kernel principle.

---

**Issue 7 — ✅ Tool call duration tracking via ToolProxy**

`ToolProxy` already intercepts every tool method call. Adding timing there gives duration metrics for every `db.execute()`, `event_bus.publish()`, and any other tool call automatically — zero changes to plugins or tools.

Two levels of detail to consider:

- **ToolProxy timing** — captures duration per tool method call. Sufficient for 80% of use cases. No external dependencies. Data can be exposed via a new `GET /system/metrics` endpoint or fed into the existing logger sink.
- **OTel driver-level connectors** (`opentelemetry-instrumentation-asyncpg`, etc.) — captures SQL query text, parameters, DB name, etc. Requires OTel (see Issue 9).

Recommendation: implement ToolProxy timing first as a standalone feature. OTel connectors are additive on top when Issue 9 is tackled.

```python
start = time.perf_counter()
result = await attr(*args, **kwargs)
duration_ms = (time.perf_counter() - start) * 1000
# emit to a registered metrics sink (same pattern as logger sinks)
```

---

**Issue 8 — ✅ Real-time causal tree via SSE**

`GET /system/traces/stream` — implemented in `domains/system/plugins/system_traces_stream_plugin.py`.

On connect sends a `snapshot` with the full current tree. Each new event emits a `node` message with `parent_id` so the client appends it to the correct place in the tree without rebuilding.

---

**Issue 9 — ✅ OpenTelemetry integration**

Add distributed tracing via OpenTelemetry without coupling the core to any specific library.

Agreed design:

- **`tools/telemetry/telemetry_tool.py`** — bootstraps the global `TracerProvider` via env vars (`OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_ENABLED`). Falls back to console exporter in development. Exposes `get_tracer(scope)` for plugins that want custom spans.
- **`ToolProxy` span factory** — proxy delegates span creation to a registrable `span_factory` callable. No OTel imports in the core. `TelemetryTool.setup()` registers the factory. `proxy_traced = False` on verbose tools (logger, telemetry itself).
- **`BaseTool.on_instrument(tracer_provider)`** — optional hook for each tool to instrument its own underlying framework (FastAPI, asyncpg, etc.). Keeps framework-specific instrumentation inside the tool that owns the framework.
- **`TelemetryTool.on_boot_complete`** — iterates all tools and calls `on_instrument`. The kernel must invoke this hook directly on tool instances (bypassing the proxy) to avoid marking tools as DEAD on instrumentation failure.
- **`HttpServerTool.on_instrument`** — calls `FastAPIInstrumentor.instrument_app(self.app)`. If the HTTP tool is swapped for Flask/Django, only this method changes.

What is gained over the existing causality system (`/system/traces`):

- Real HTTP span with method, path, status code, and latency as the trace root
- Tool call durations via the proxy
- Export to external platforms: Jaeger, Grafana Tempo, Datadog, etc.
- Cross-service distributed tracing via `traceparent` header propagation

Depends on Issue 7 (ToolProxy timing) as a prerequisite.

Boton de busqueda de conexiones

---

## AI Tool — Pending

**Issue 14 — AI token usage tracking**

Track input/output/cache tokens per AI call to give the user visibility into consumption and estimated cost.

**Steps:**

1. `ai_tool.py` — add internal `_complete_raw()` that returns `{text, usage}`. Keep `complete()` returning `str` to avoid breaking callers. Expose `complete_with_usage()` for callers that want metrics.

2. Migration `ai_usage_log` table:
   ```sql
   CREATE TABLE ai_usage_log (
       id                INTEGER PRIMARY KEY AUTOINCREMENT,
       context           TEXT NOT NULL,  -- 'chat' | 'moderation'
       model             TEXT NOT NULL,
       prompt_tokens     INTEGER,
       completion_tokens INTEGER,
       cached_tokens     INTEGER,
       created_at        TEXT NOT NULL DEFAULT (datetime('now'))
   );
   ```

3. `IAChatPlugin` and `AiModPlugin` — call `complete_with_usage()` and fire-and-forget persist to DB. Must handle providers that don't return `usage` gracefully (Ollama sometimes omits it).

4. `GET /ai/usage` endpoint — returns aggregates: total tokens today/week/all-time, breakdown by context (chat vs moderation), estimated cost per provider (cost-per-token config per provider).

5. `AIUsageStats.svelte` — panel in `/ai` page showing the stats. Add to `ai/+page.svelte` as optional import (modular, removable).

**Complexity:** Medium. Main risk is provider inconsistency — not all return `usage`, and `cached_tokens` is only available on OpenAI and Anthropic. Must fail silently when absent.