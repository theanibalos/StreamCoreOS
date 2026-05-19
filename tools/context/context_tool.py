import os
import re
from core.base_tool import BaseTool


class ContextTool(BaseTool):
    @property
    def name(self) -> str:
        return "context_manager"

    def setup(self):
        pass

    def get_interface_description(self) -> str:
        return """
        Context Manager Tool (context_manager):
        - PURPOSE: Automatically manages and generates live AI contextual documentation.
        - CAPABILITIES:
            - Reads the system registry.
            - Exports active tools, health status, and domain models to AI_CONTEXT.md.
            - Generates per-domain AI_CONTEXT.md files inside each domain folder.
        """

    def _scan_domain_models(self, registry):
        """
        Scans domains/*/models/*.py and registers them to the registry.
        Moved here from the Kernel to preserve the blind-kernel principle.
        """
        domains_dir = os.path.abspath("domains")
        if not os.path.exists(domains_dir):
            return
        for domain_name in sorted(os.listdir(domains_dir)):
            models_dir = os.path.join(domains_dir, domain_name, "models")
            if not os.path.isdir(models_dir):
                continue
            for filename in sorted(os.listdir(models_dir)):
                if not filename.endswith(".py") or filename == "__init__.py":
                    continue
                filepath = os.path.join(models_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        registry.register_domain_metadata(domain_name, f"model_{filename}", f.read())
                except Exception as e:
                    print(f"[ContextTool] Error reading model {filepath}: {e}")

    def on_boot_complete(self, container):
        registry = container.registry
        self._scan_domain_models(registry)
        self._generate_global_manifest(container)

    # ── Global manifest ───────────────────────────────────────────────────────

    def _generate_global_manifest(self, container):
        manifest = "# 📜 SYSTEM MANIFEST\n\n"
        manifest += "> This file is ALL you need to build a plugin. For advanced topics (testing, observability, creating tools), see [INSTRUCTIONS_FOR_AI.md](INSTRUCTIONS_FOR_AI.md).\n\n"

        manifest += self._generate_plugin_quick_start()

        manifest += "## 🛠️ Quick Architecture Ref\n"
        manifest += "- **Pattern**: `__init__` (DI) -> `on_boot` (Register) -> handler methods (Action).\n"
        manifest += "- **Injection**: Tools are injected by name in the constructor.\n\n"

        manifest += "## 🛠️ Available Tools\n"
        manifest += "Check method signatures before implementation.\n\n"

        for name in container.list_tools():
            try:
                tool = container.get(name)
                description = str(tool.get_interface_description()).strip()
                if not description:
                    print(f"[ContextTool] WARNING: Tool '{name}' has no interface description. "
                          f"Update get_interface_description() in its class.")
                status_emoji = "✅" if tool else "❌"
                manifest += f"### 🔧 Tool: `{name}` (Status: {status_emoji})\n"
                manifest += "```text\n"
                manifest += description
                manifest += "\n```\n\n"
            except Exception as e:
                manifest += f"### 🔧 Tool: `{name}` (Status: ❌)\n"
                manifest += f"Error extracting info: {e}\n\n"

        manifest += "## 📦 Domains\n\n"

        dump = container.registry.get_system_dump()
        plugins_by_domain: dict[str, list[tuple[str, dict]]] = {}
        for plugin_name, info in dump.get("plugins", {}).items():
            domain = info.get("domain")
            if domain:
                plugins_by_domain.setdefault(domain, []).append((plugin_name, info))

        for domain in sorted(plugins_by_domain.keys()):
            plugins = plugins_by_domain[domain]
            plugin_names = [p[0] for p in plugins]

            all_deps: set[str] = set()
            for _, info in plugins:
                all_deps.update(info.get("dependencies", []))

            endpoints = self._get_domain_endpoints(domain)
            emitted_map = self._scan_published_events(domain)
            consumed = self._get_consumed_events(plugin_names, container)
            tables = self._get_domain_tables(domain)

            manifest += f"### `{domain}`\n"
            if tables:
                for table in tables:
                    fields = self._get_model_fields(domain, table)
                    fields_str = ", ".join(f"{name} ({type_})" for name, type_ in fields.items())
                    manifest += f"- **Table `{table}`**: {fields_str}\n"
            else:
                manifest += "- **Tables**: none\n"

            if endpoints:
                manifest += f"- **Endpoints**: {', '.join(endpoints)}\n"
            else:
                manifest += "- **Endpoints**: none\n"
            
            if emitted_map:
                emitted_strs = [f"`{name}` ({', '.join(sorted(keys))})" for name, keys in sorted(emitted_map.items())]
                manifest += f"- **Events emitted**: {', '.join(emitted_strs)}\n"
            else:
                manifest += "- **Events emitted**: none\n"

            manifest += f"- **Events consumed**: {', '.join(sorted(consumed)) if consumed else 'none'}\n"
            manifest += f"- **Dependencies**: {', '.join(sorted(all_deps)) if all_deps else 'none'}\n"
            manifest += f"- **Plugins**: {', '.join(sorted(plugin_names))}\n\n"

        try:
            with open("AI_CONTEXT.md", "w", encoding="utf-8") as f:
                f.write(manifest)
        except Exception as e:
            print(f"[ContextTool] Error writing AI_CONTEXT.md: {e}")

    def _generate_plugin_quick_start(self) -> str:
        return """## ⚡ Plugin Quick Start

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

"""

    def _get_domain_endpoints(self, domain: str) -> list[str]:
        """
        Static analysis of plugin source files — same approach as _scan_published_events.
        Handles both call styles:
          positional: add_endpoint("/path", "METHOD", ...)
          keyword:    add_endpoint(path="/path", method="METHOD", ...)
        """
        endpoints: set[str] = set()
        plugins_dir = os.path.join("domains", domain, "plugins")
        if not os.path.isdir(plugins_dir):
            return []
        for filename in os.listdir(plugins_dir):
            if not filename.endswith(".py"):
                continue
            try:
                with open(os.path.join(plugins_dir, filename), "r", encoding="utf-8") as f:
                    content = f.read()
                # Positional: add_endpoint("/path", "METHOD", ...)
                for m in re.finditer(
                    r'add_endpoint\(\s*["\']([^"\']+)["\'],\s*["\']([A-Z]+)["\']', content
                ):
                    endpoints.add(f"{m.group(2)} {m.group(1)}")
                # Keyword: add_endpoint(path="/path", ..., method="METHOD", ...)
                for call in re.findall(r'add_endpoint\(([^)]+)\)', content, re.DOTALL):
                    path_m = re.search(r'path\s*=\s*["\']([^"\']+)["\']', call)
                    method_m = re.search(r'method\s*=\s*["\']([A-Z]+)["\']', call)
                    if path_m and method_m:
                        endpoints.add(f"{method_m.group(1)} {path_m.group(1)}")
            except Exception:
                pass
        return sorted(endpoints)

    def _get_consumed_events(self, plugin_names: list[str], container) -> set[str]:
        try:
            event_bus = container.get("event_bus")
            consumed = set()
            for event, subs in event_bus.get_subscribers().items():
                if event.startswith("_reply."):
                    continue
                for sub in subs:
                    if sub.split(".")[0] in plugin_names:
                        consumed.add(event)
                        break
            return consumed
        except Exception:
            return set()

    def _scan_published_events(self, domain: str) -> dict[str, set[str]]:
        """
        Scans plugin files to find published events and their payload keys.
        Returns a dict: { "event.name": {"key1", "key2", ...} }
        """
        event_map: dict[str, set[str]] = {}
        plugins_dir = os.path.join("domains", domain, "plugins")
        if not os.path.isdir(plugins_dir):
            return event_map
        
        # Regex to find: .publish("event.name", {"key": val, 'key2': val2})
        # This is a basic extractor for simple dict literals.
        publish_pattern = re.compile(r'\.publish\(\s*["\']([^"\']+)["\']\s*,\s*(\{.*?\})', re.DOTALL)
        
        for filename in os.listdir(plugins_dir):
            if not filename.endswith(".py"):
                continue
            try:
                with open(os.path.join(plugins_dir, filename), "r", encoding="utf-8") as f:
                    content = f.read()
                
                for event_name, payload_str in publish_pattern.findall(content):
                    keys = set(re.findall(r'["\']([^"\']+)["\']\s*:', payload_str))
                    if event_name not in event_map:
                        event_map[event_name] = keys
                    else:
                        event_map[event_name].update(keys)
            except Exception:
                pass
        return event_map

    def _get_domain_tables(self, domain: str) -> list[str]:
        models_dir = os.path.join("domains", domain, "models")
        if not os.path.isdir(models_dir):
            return []
        return sorted([
            f.replace(".py", "")
            for f in os.listdir(models_dir)
            if f.endswith(".py") and f != "__init__.py"
        ])

    def _get_model_fields(self, domain: str, table: str) -> dict[str, str]:
        """
        Parses the model file to extract fields and their types.
        Simple regex-based parsing for MicroCoreOS models.
        """
        model_path = os.path.join("domains", domain, "models", f"{table}.py")
        if not os.path.exists(model_path):
            return {}
        
        fields = {}
        try:
            with open(model_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Match fields in Pydantic model (e.g. name: str, price: float = 0.0)
            # We skip 'id' as it's auto-generated.
            for line in content.split("\n"):
                line = line.strip()
                if ":" in line and not line.startswith("class") and not line.startswith("def"):
                    parts = line.split(":")
                    name = parts[0].strip()
                    if name == "id":
                        continue
                    type_part = parts[1].split("=")[0].strip()
                    fields[name] = type_part
        except Exception:
            pass
        return fields
