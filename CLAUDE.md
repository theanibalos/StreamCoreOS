# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Reading Path (minimize token usage)

**To write a plugin or domain**: Read `AI_CONTEXT.md` + the entity model in `domains/{domain}/models/`. Nothing else.
**For testing, observability, or creating tools**: Read `INSTRUCTIONS_FOR_AI.md`.

## Commands

```bash
uv run main.py                              # Run the app
uv run pytest                               # Run all tests
uv run pytest tests/test_file.py            # Run single test
docker compose -f dev_infra/docker-compose.yml up -d  # Dev infra
```

## Essential Rules

1. **Never modify `main.py`** — Kernel auto-discovers everything.
2. **1 file = 1 feature** — Plugins in `domains/{domain}/plugins/`.
3. **DI by name** — `__init__` parameter names match tool `name` properties.
4. **Entity in models/ = DB mirror only** — Request AND response schemas go inline in the plugin.
5. **No cross-domain imports** — Use `event_bus` for communication.
6. **Return format**: `{"success": bool, "data": ..., "error": ...}`.
7. **Runner**: Always `uv run`.

> Advanced topics (testing, observability, creating tools): `INSTRUCTIONS_FOR_AI.md`.

## Plugin Import Rules

Un plugin SOLO puede importar de stdlib y de `core.base_plugin`. Nada más.

```python
# ✅ Permitido
import asyncio
import json
from core.base_plugin import BasePlugin

# ❌ NUNCA — aunque sea "infraestructura compartida"
from tools.xxx import YYY
from tools.xxx.errors import SomeError
from domains.xxx import YYY

# ❌ NUNCA — ni siquiera entre plugins del mismo dominio
from domains.tts_chat.plugins.otro_plugin import algo
```

Las tools y servicios se reciben **exclusivamente por DI** en `__init__`. Si necesitas manejar errores de una tool, usa duck typing:

```python
except Exception as e:
    code = getattr(e, "code", None)   # no import necesario
```

## Pre-commit Hook (pendiente de implementar)

Para enforcer las reglas de imports automáticamente sin depender de que la IA las recuerde, implementar un script `.git/hooks/pre-commit` que:
1. Busque todos los archivos en `domains/*/plugins/*.py`
2. Rechace el commit si alguno contiene `from tools.` o `from domains.` en sus imports
3. Muestre qué archivo y línea viola la regla

Esto convierte una regla de honor en una regla de máquina.
