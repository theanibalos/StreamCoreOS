# MicroCoreOS — AI Agent Guide

This file is the entry point for any AI agent (Cursor, Copilot, Gemini, GPT, etc.) working in this codebase.

## What this project is

A Python micro-kernel where the framework auto-discovers tools and plugins by folder convention.
No manual registration. No routers. No controllers. Just files in the right place.

## Reading Path (read in this order, stop when you have enough)

1. **`AI_CONTEXT.md`** — Auto-generated on every `uv run main.py`. Contains the live inventory of all available tools (with exact method signatures) and all active domains. **Start here for any task.**
2. **Entity model** — `domains/{domain}/models/{name}.py`. Read only the domain you're working on.
3. **`INSTRUCTIONS_FOR_AI.md`** — Full rules, templates, and anti-patterns. Read for edge cases or when creating a new domain.

> Do not read the entire codebase. Two files are enough for 90% of tasks.

## Commands

```bash
uv run main.py                                          # Run the app (also regenerates AI_CONTEXT.md)
uv run pytest                                           # Run all tests
uv run pytest tests/test_file.py                        # Run single test
docker compose -f dev_infra/docker-compose.yml up -d    # Start dev infrastructure (PostgreSQL)
```

## Non-negotiable Rules

1. **Never modify `main.py`** — the Kernel auto-discovers everything.
2. **1 file = 1 feature** — each plugin lives in `domains/{domain}/plugins/{feature}_plugin.py`.
3. **DI by parameter name** — `__init__(self, http, db, logger)` injects the tools named `http`, `db`, `logger`.
4. **Schemas inline** — request and response schemas go at the top of the plugin file, never in `models/`.
5. **No cross-domain Python imports** — No `from domains.X import ...`. Async/reactive events go through `event_bus`. As a modular monolith, shared SQLite database tables can be queried directly via `db`.
6. **Return envelope** — always `{"success": bool, "data": ..., "error": ...}`.
7. **Placeholders** — always `$1, $2, $3...` in SQL (PostgreSQL-style; SQLite converts internally).

## Minimal Plugin Template

```python
from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin

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
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
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
            return {"success": True, "data": {"id": new_id, "name": req.name}}
        except Exception as e:
            self.logger.error(f"Failed: {e}")
            return {"success": False, "error": str(e)}
```

## Anti-Patterns (read before writing any code)

| Wrong | Correct |
|-------|---------|
| `from domains.users.models.user import UserEntity` inside another domain | Define your schema inline in the plugin |
| `container.get("event_bus")` inside a tool's `on_boot_complete` | Use a Kernel lifecycle hook — `container.get()` inside a tool is a hidden cross-tool import |
| `from tools.http_server.http_server_tool import HttpServerTool` | Use DI: `def __init__(self, http)` |
| Request field without `Field(...)`: `name: str` | `name: str = Field(min_length=1, max_length=100)` |
| `add_endpoint(...)` without `response_model=` | Always pass `response_model=YourResponse` |
| `?` placeholders in SQL | `$1, $2, $3...` |
| Logic or I/O in `__init__` | Move to `on_boot()` or `execute()` |
| Returning a Pydantic model instance in the response dict | Call `.model_dump()` first |

## Checklist Before Submitting

- [ ] `main.py` untouched
- [ ] No imports from other domains
- [ ] Entity in `models/` mirrors DB columns only — schemas are inline in the plugin
- [ ] All request fields have `Field(...)` constraints
- [ ] `response_model=` passed to `add_endpoint`
- [ ] Test file exists at `tests/test_{feature}_plugin.py`
- [ ] `uv run main.py` starts without errors

## Where to find examples

| Pattern | File |
|---------|------|
| CRUD + events | `domains/users/plugins/create_user_plugin.py` |
| Protected endpoint (JWT) | `domains/users/plugins/get_me_plugin.py` |
| Auth + cookies | `domains/users/plugins/login_plugin.py` |
| Minimal (no DB) | `domains/ping/plugins/ping_plugin.py` |
| Introspection | `domains/system/plugins/system_status_plugin.py` |
