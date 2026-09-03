# 🤖 AI Agent Implementation Guide — Advanced Reference

> **For building plugins, read `AI_CONTEXT.md` only.** This file is for advanced topics: testing, observability, creating new tools, and edge cases.

## ❌ Anti-Patterns (Common AI Mistakes)

These are the most frequent errors. Check these first before writing any code.

| Wrong | Correct | Why |
|-------|---------|-----|
| `from domains.users.models.user import UserEntity` inside `orders` plugin | Define `OrderData` inline | No cross-domain imports |
| `class CreateUserRequest(BaseModel): name: str` (bare field) | `name: str = Field(min_length=1)` | FastAPI won't validate without constraints |
| `add_endpoint("/users", "POST", self.execute, ...)` without `response_model=` | Always pass `response_model=CreateUserResponse` | No OpenAPI docs generated |
| Logic inside `__init__` | Move to `on_boot()` or `execute()` | `__init__` is DI only |
| `import asyncio; asyncio.run(...)` inside a plugin | `await` or schedule via `scheduler` | Already inside an event loop |
| `?` placeholders in SQL | `$1, $2, $3...` | PostgreSQL-style; SQLite converts automatically |
| Returning the full Entity object (including `password_hash`) | Define a response schema with only the fields you expose | Leaks sensitive data |
| `from tools.http_server.http_server_tool import HttpServerTool` | Use DI: `def __init__(self, http)` | Hardcoded imports break tool swapping |
| Inside a tool's `on_boot_complete`: `container.get("event_bus")` | Add a new lifecycle hook to the Kernel instead | `container.get()` inside a tool is a hidden cross-tool import — same violation, different syntax. The only exception is `context_manager`, whose explicit purpose is system introspection. |
| Creating a service class or router | Use a plugin directly | No framework patterns |

---

## ⚠️ CRITICAL RULES (DO NOT IGNORE)

1. **`main.py` is sacred** — Never modify. It only boots the Kernel.
2. **No hardcoded imports** — Plugins request tools by `__init__` parameter name matching the tool's `name` property.
3. **No framework patterns** — No Routers, Controllers, or Services. Only Tools (infrastructure) and Plugins (business logic).
4. **No cross-domain Python imports** — No `from domains.X import ...`. Async/reactive inter-domain flows use `event_bus`. As a modular monolith, database tables are shared in SQLite and queryable directly via `db`.
5. **Tools never import other tools** — Move cross-tool orchestration to a plugin.
6. **No logic in `__init__` or `setup()`** — DI and resource allocation only.
7. **`async def` for I/O, `def` for CPU** — Kernel auto-threads sync methods. Never `time.sleep()` in async.
8. **Return format**: Always `{"success": bool, "data": ..., "error": ...}`.
9. **Runner**: Always use `uv run main.py` / `uv run pytest`.

---

## 🧭 Navigation

| Task | Section |
|---|---|
| New feature on existing domain | [Plugin](#-new-plugin) |
| New functional area from scratch | [Domain](#-new-domain) |
| New infrastructure capability | [Tool](#-new-tool) |

---

## 🧩 New Domain

Folder structure:
```
domains/{name}/
  __init__.py
  models/{name}.py        ← DB Entity only (mirrors the table)
  migrations/001_xxx.sql  ← Raw SQL, auto-executed on boot
  plugins/                ← 1 file = 1 feature
```

*Domains MUST NOT import Python code/models from each other.*

### Entity vs Request Schema — where each lives

| What | Where |
|---|---|
| `<Name>Entity` — DB mirror | `domains/{name}/models/{name}.py` — only changes when the DB table changes |
| Request schema | **Top of the plugin file** that owns it — never in `models/` |
| Response schema | **Top of the plugin file** that owns it — never import the Entity for this |

**Why:** Each plugin is self-contained. An AI reads entity + its plugin, nothing else. Multiple AI agents work in parallel without conflicts.

**Response schema rules:**
- Define only the fields you actually return — never expose `password_hash` or other sensitive entity fields.
- `data` field type must match exactly what `execute()` returns, not the full Entity.
- Always pass `response_model=` to `add_endpoint` — this is what generates complete OpenAPI docs.

### The AI-Driven Build Sequence

1. **Model** → `domains/{name}/models/{name}.py` (Pydantic entity mirroring the DB table)
2. **Migration** → `domains/{name}/migrations/001_create_{name}.sql` (Raw SQL, `$1, $2...` placeholders)
3. **Plugins** → One file per use case in `domains/{name}/plugins/`

> The `db` tool auto-runs pending `.sql` migration files on boot.

---

## ⚡ New Plugin

**Location**: `domains/{domain}/plugins/{feature}_plugin.py`
**Rule**: 1 File = 1 Feature. Schema defined inline.

### Validation standard — always use `Field`

All request schemas MUST use `pydantic.Field` for constraints. This is the only accepted pattern:

```python
from pydantic import BaseModel, Field, EmailStr

class CreateProductRequest(BaseModel):
    name: str        = Field(min_length=1, max_length=100)
    price: float     = Field(gt=0)
    sku: str | None  = Field(default=None, pattern=r"^[A-Z0-9\-]+$")
```

FastAPI validates automatically and returns 422 with a clear error — no try/except needed for input validation.
Never use bare `str`, `int`, or `float` fields without constraints in a request schema.

---

```python
from typing import Optional
from pydantic import BaseModel, Field, EmailStr
from microcoreos.base_plugin import BasePlugin

# ── Request schema lives HERE, not in models/ ──────────────
class CreateProductRequest(BaseModel):
    name: str   = Field(min_length=1, max_length=100)
    price: float = Field(gt=0)

# ── Response schema lives HERE — define only what you return ─
class ProductData(BaseModel):
    id: int
    name: str
    price: float

class CreateProductResponse(BaseModel):
    success: bool
    data: Optional[ProductData] = None
    error: Optional[str] = None

class CreateProductPlugin(BasePlugin):
    def __init__(self, logger, event_bus, http, db):
        # 1. DI Phase — save tools, no logic
        self.logger = logger
        self.bus = event_bus
        self.http = http
        self.db = db

    async def on_boot(self):
        # 2. Registration Phase
        self.http.add_endpoint(
            path="/products",
            method="POST",
            handler=self.execute,
            tags=["Products"],
            request_model=CreateProductRequest,
            response_model=CreateProductResponse,
        )
        await self.bus.subscribe("order.created", self.on_order_created)

    async def execute(self, data: dict, context=None):
        # 3. Action Phase — context has set_cookie(), set_header(), set_status()
        try:
            req = CreateProductRequest(**data)
            product_id = await self.db.execute(
                "INSERT INTO products (name, price) VALUES ($1, $2) RETURNING id",
                [req.name, req.price]
            )
            await self.bus.publish("product.created", {"id": product_id})
            return {"success": True, "data": {"id": product_id, "name": req.name, "price": req.price}}
        except Exception as e:
            self.logger.error(f"Failed to create product: {e}")
            return {"success": False, "error": str(e)}

    async def on_order_created(self, data: dict) -> None:
        # Event subscriber — only data dict, no context.
        # Return a dict to participate in request() RPC.
        self.logger.info(f"Order received: {data}")
```

> **Hybrid Power**: `async def` for I/O. `def` for CPU-heavy work — Kernel auto-offloads to thread pool.

---

## 🔧 New Tool

**Location**: `tools/{name}/{name}_tool.py`
**Rule**: Stateless, isolated, self-documented.

### Tool availability contract — `setup()` decides the failure model

The tool is the only one that knows whether its unavailability is recoverable. This determines how `setup()` must behave:

| Type | `setup()` behavior | Example |
|------|--------------------|---------|
| **Critical infrastructure** | Raises exception if unavailable → kernel marks FAIL → dependent plugins marked DEAD | PostgreSQL, Redis |
| **External / eventually available** | Never raises → kernel marks READY → plugins load → each call handles unavailability gracefully | Government APIs, third-party services |

**Critical tool** — `setup()` must fail fast with a timeout:
```python
async def setup(self) -> None:
    try:
        self._client = await asyncio.wait_for(self._connect(), timeout=5)
    except asyncio.TimeoutError:
        raise ConnectionError("Service unavailable after 5s")
    except Exception as e:
        raise ConnectionError(f"Cannot connect: {e}") from e
```

**External / eventually available tool** — `setup()` never raises, each method retries:
```python
async def setup(self) -> None:
    try:
        await asyncio.wait_for(self._ping(), timeout=5)
        self._available = True
    except Exception:
        self._available = False
        print("[MyTool] ⚠️ Not available at startup. Will connect when ready.")

async def call(self, params):
    if not self._available:
        await self._try_reconnect()
    if not self._available:
        raise ServiceUnavailableError("Service not available")
    # ... real call
```

The plugin handles `ServiceUnavailableError` like any other exception and returns `{"success": false, "error": "..."}`. No special kernel support needed.

**Never add a global timeout in the kernel or HTTP layer** — long operations (video processing, bulk exports) are legitimate. Each tool is responsible for its own timeouts.

```python
from microcoreos.base_tool import BaseTool

class MyServiceTool(BaseTool):
    @property
    def name(self) -> str:
        return "my_service"

    def setup(self):
        # Resource allocation: connections, env vars
        print("[MyService] Initializing...")

    def get_interface_description(self) -> str:
        # AI-readable manual — exact signatures
        return """
        My Service Tool (my_service):
        - PURPOSE: What it does.
        - CAPABILITIES:
            - do_something(arg1, arg2): What this method does.
        """

    def on_boot_complete(self, container):
        # Optional: all tools ready, can call container.get('name')
        pass

    def shutdown(self):
        # Cleanup: close connections, stop threads
        pass
```

---

## 🔄 Lifecycle

### Tool: `setup()` → `on_boot_complete(container)` → `shutdown()`
### Plugin: `__init__(tools)` → `on_boot()` → handler methods → `shutdown()`

---

## 🧪 Testing

Mock all tools, instantiate the plugin directly:

```python
@pytest.mark.anyio
async def test_example():
    plugin = MyPlugin(
        http=MagicMock(),
        db=AsyncMock(),
        event_bus=AsyncMock(),
        logger=MagicMock()
    )
    result = await plugin.execute({"key": "value"})
    assert result["success"] is True
```

---

## 📡 Observability Capabilities

### Tool call metrics via `registry`

Every tool call is automatically timed by ToolProxy. Access in any plugin:

```python
class MyMetricsPlugin(BasePlugin):
    def __init__(self, registry, event_bus):
        self.registry = registry
        self.bus = event_bus

    async def on_boot(self):
        # Real-time sink — called synchronously on every tool call, keep it fast
        self.registry.add_metrics_sink(self._on_metric)

    def _on_metric(self, record: dict):
        # record = {tool, method, duration_ms, success, timestamp}
        if record["duration_ms"] > 500:
            # fire-and-forget — don't await inside a sync sink
            import asyncio
            asyncio.create_task(self.bus.publish("alert.slow_tool", record))

    async def execute(self, data: dict, context=None):
        # Snapshot of last 1000 records
        records = self.registry.get_metrics()
        return {"success": True, "data": records}
```

### OpenTelemetry via `telemetry`

All tool calls get spans automatically when `OTEL_ENABLED=true` — no plugin changes needed.

For **custom spans** inside a plugin:

```python
class OrderPlugin(BasePlugin):
    def __init__(self, telemetry, db, http):
        self.telemetry = telemetry  # inject by name
        self.db = db
        self.http = http

    async def on_boot(self):
        self.http.add_endpoint("/orders", "POST", self.execute, tags=["Orders"])

    async def execute(self, data: dict, context=None):
        tracer = self.telemetry.get_tracer("orders")
        with tracer.start_as_current_span("process_order"):
            result = await self.db.execute("INSERT INTO orders ...")
            return {"success": True, "data": {"id": result}}
```

`get_tracer()` returns a **no-op tracer** when OTel is disabled — safe to use unconditionally.

### Proactive health check

To monitor tools that may fail silently (e.g. network databases), use the `ToolHealthPlugin` pattern already available in `domains/system/plugins/tool_health_plugin.py`. Configure the interval:
```bash
HEALTH_CHECK_INTERVAL=30  # seconds, default: 30
```

---

## 📂 Reference Gallery

- **CRUD + Events**: [create_user_plugin.py](domains/users/plugins/create_user_plugin.py)
- **Protected endpoint (JWT)**: [get_me_plugin.py](domains/users/plugins/get_me_plugin.py)
- **Auth flow**: [login_plugin.py](domains/users/plugins/login_plugin.py)

---

## 🗄️ Swapping the Database Engine

The `db` injection key is the contract. Whichever tool has `name = "db"` is the active database — plugins never change.

**To swap engines:**
1. Move the active tool from `tools/` to `extras/available_tools/`.
2. Move the new tool from `extras/available_tools/` to `tools/`.
3. Set its `name` to `"db"`.
4. Rewrite migration files — DDL is engine-specific (`SERIAL` vs `INTEGER PRIMARY KEY AUTOINCREMENT`, `TIMESTAMPTZ` vs `TEXT`, etc.). Migration SQL is cheap to regenerate.
5. Plugins do not change.

Both tools share the identical public contract (`query`, `query_one`, `execute`, `execute_many`, `transaction`, `health_check`) and use `$1, $2...` placeholders. SQLite converts them internally to `?`.

---
*`AI_CONTEXT.md` is auto-generated on every boot by the `context_manager` tool. It contains the live inventory of tools and domain models.*
