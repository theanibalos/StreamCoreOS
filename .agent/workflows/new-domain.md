---
description: Create a complete new domain with entity, migration, and CRUD plugins
---

# New Domain Workflow

Creates a full domain from scratch: entity model, SQL migration, and one plugin per use case.

## Prerequisites
- Read `AI_CONTEXT.md` for available tools.
- Read `INSTRUCTIONS_FOR_AI.md` for rules and templates.

## Steps

### 1. Create the domain folder structure

```bash
// turbo
mkdir -p domains/{name}/models domains/{name}/migrations domains/{name}/plugins
```

Create `domains/{name}/__init__.py`:
```python
# Auto-discovered by the Kernel. No manual registration needed.
```

### 2. Create the Entity model

File: `domains/{name}/models/{name}.py`

This file contains ONE thing: the Pydantic model that mirrors the database table exactly.

```python
from pydantic import BaseModel

class {Name}Entity(BaseModel):
    id: int | None = None
    # Add fields that match the DB columns exactly
    # Use the DB column names (e.g. password_hash, not password)
```

### 3. Create the SQL migration

File: `domains/{name}/migrations/001_create_{name}_table.sql`

Write raw SQL that creates the table. Use `$1, $2...` placeholders in queries (PostgreSQL-style, auto-converted for SQLite).

```sql
CREATE TABLE IF NOT EXISTS {name}s (
    id SERIAL PRIMARY KEY,
    -- columns matching the entity model
    created_at TIMESTAMP DEFAULT NOW()
);
```

### 4. Create plugins (1 file = 1 use case)

For each operation (create, get_all, get_by_id, update, delete), create a separate plugin file in `domains/{name}/plugins/`.

**Critical rules**:
- Define the **request schema** (what the HTTP client sends) at the **top of the plugin file**, NOT in the models folder.
- Define the **response schema** (what the HTTP client receives) at the **top of the plugin file** too — never import the Entity for this; only expose the fields you actually return.
- Always pass `response_model=` to `add_endpoint` — this generates complete OpenAPI docs.

Example for create:

File: `domains/{name}/plugins/create_{name}_plugin.py`

```python
from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin

# ── Request schema lives HERE ──────────────────────
class Create{Name}Request(BaseModel):
    # Only input fields — no id, no internal fields
    field1: str
    field2: int

# ── Response schema lives HERE ─────────────────────
class {Name}Data(BaseModel):
    id: int
    field1: str
    field2: int

class Create{Name}Response(BaseModel):
    success: bool
    data: Optional[{Name}Data] = None
    error: Optional[str] = None

class Create{Name}Plugin(BasePlugin):
    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/{name}s", "POST", self.execute,
            tags=["{Name}s"], request_model=Create{Name}Request,
            response_model=Create{Name}Response,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = Create{Name}Request(**data)
            new_id = await self.db.execute(
                "INSERT INTO {name}s (field1, field2) VALUES ($1, $2) RETURNING id",
                [req.field1, req.field2]
            )
            self.logger.info(f"{Name} created with ID {new_id}")
            await self.bus.publish("{name}.created", {"id": new_id})
            return {"success": True, "data": {"id": new_id, "field1": req.field1, "field2": req.field2}}
        except Exception as e:
            self.logger.error(f"Failed to create {name}: {e}")
            return {"success": False, "error": str(e)}
```

Repeat for: `get_{name}s_plugin.py`, `get_{name}_by_id_plugin.py`, `update_{name}_plugin.py`, `delete_{name}_plugin.py`.

### 5. Verify

```bash
// turbo
uv run main.py
```

Check that:
- Migration ran successfully (look for `[Migration] ✅` in logs)
- Endpoints appear in the Swagger UI at `http://localhost:8000/docs`
- `AI_CONTEXT.md` was regenerated with the new domain

### 6. Generate tests

Create `tests/test_{name}_plugin.py` with one test per plugin. Mock all tools:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock
from domains.{name}.plugins.create_{name}_plugin import Create{Name}Plugin

@pytest.mark.anyio
async def test_create_{name}_success():
    plugin = Create{Name}Plugin(
        http=MagicMock(),
        db=AsyncMock(return_value=1),
        event_bus=AsyncMock(),
        logger=MagicMock(),
    )
    result = await plugin.execute({"field1": "value", "field2": 42})
    assert result["success"] is True
    assert result["data"]["id"] == 1

@pytest.mark.anyio
async def test_create_{name}_db_error():
    plugin = Create{Name}Plugin(
        http=MagicMock(),
        db=AsyncMock(side_effect=Exception("DB down")),
        event_bus=AsyncMock(),
        logger=MagicMock(),
    )
    result = await plugin.execute({"field1": "value", "field2": 42})
    assert result["success"] is False
    assert "DB down" in result["error"]
```

Run with `uv run pytest tests/test_{name}_plugin.py`.
