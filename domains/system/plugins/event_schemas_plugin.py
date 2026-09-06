import ast
import importlib.util
import os
from typing import Optional
from pydantic import BaseModel
from microcoreos import BasePlugin


class EventSchemasData(BaseModel):
    schemas: dict = {}


class EventSchemasResponse(BaseModel):
    success: bool
    data: Optional[EventSchemasData] = None
    error: Optional[str] = None


class EventSchemasPlugin(BasePlugin):
    """
    GET /system/events/schemas — the event contract catalog: one JSON Schema
    per published event, generated from the Payload(BaseModel) each publisher
    plugin owns.

    This is the seed of a schema registry: when the event bus is swapped to a
    distributed broker (Kafka — Roadmap Issue 18), these are exactly the
    schemas the registry ingests, with zero plugin changes.

    Self-contained and independent: reads metadata from the registry if available,
    or directly discovers models from domain plugins via AST. It never breaks
    if a linter is absent or deleted.
    """

    def __init__(self, container, http, logger):
        self.registry = container.registry
        self.http = http
        self.logger = logger
        self._cache = None

    async def on_boot(self):
        self.http.add_endpoint(
            "/system/events/schemas", "GET", self.get_schemas,
            tags=["System"],
            response_model=EventSchemasResponse,
        )
        self.http.add_endpoint(
            "/api/system/events/schemas", "GET", self.get_schemas,
            tags=["System"],
            response_model=EventSchemasResponse,
        )

    async def get_schemas(self, data: dict, context=None):
        try:
            if self._cache is None:
                self._cache = self._build_catalog()
            return {"success": True, "data": {"schemas": self._cache}}
        except Exception as e:
            self.logger.error(f"[EventSchemas] Failed to build catalog: {e}")
            return {"success": False, "error": "Could not build event schema catalog"}

    def _build_catalog(self) -> dict:
        meta = self.registry.get_domain_metadata().get("devtools", {})
        entries = meta.get("event_payload_models")
        if not entries:
            entries = self._discover_payload_models()

        catalog: dict[str, list] = {}
        for entry in entries:
            model = self._load_model(entry["domain"], entry["file"], entry["model"])
            if model is None:
                continue
            record = {
                "model": entry["model"],
                "domain": entry["domain"],
                "file": entry["file"],
                "json_schema": model.model_json_schema(),
            }
            bucket = catalog.setdefault(entry["event"], [])
            if not any(r["model"] == record["model"] and r["file"] == record["file"]
                       for r in bucket):
                bucket.append(record)
        return catalog

    def _load_model(self, domain: str, filename: str, class_name: str):
        path = os.path.join("domains", domain, "plugins", filename)
        try:
            spec = importlib.util.spec_from_file_location(
                f"event_schemas_{domain}_{filename[:-3]}", path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls = getattr(module, class_name, None)
            if isinstance(cls, type) and issubclass(cls, BaseModel):
                return cls
            self.logger.warning(
                f"[EventSchemas] '{class_name}' in {path} is not a BaseModel — skipped"
            )
        except Exception as e:
            self.logger.warning(f"[EventSchemas] Could not load {class_name} from {path}: {e}")
        return None

    def _discover_payload_models(self) -> list[dict]:
        """Discover event payload models directly from domain plugins via AST.

        Guarantees that EventSchemasPlugin works independently without depending
        on any devtools linter running at boot.
        """
        entries = []
        domains_dir = os.path.abspath("domains")
        if not os.path.isdir(domains_dir):
            from pathlib import Path
            candidate = Path(__file__).resolve().parents[2]
            if candidate.is_dir() and candidate.name == "domains":
                domains_dir = str(candidate)

        if not os.path.isdir(domains_dir):
            return entries

        for domain in sorted(os.listdir(domains_dir)):
            plugins_dir = os.path.join(domains_dir, domain, "plugins")
            if not os.path.isdir(plugins_dir):
                continue
            for filename in sorted(os.listdir(plugins_dir)):
                if not filename.endswith(".py"):
                    continue
                filepath = os.path.join(plugins_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read())
                except Exception:
                    continue

                models = {
                    node.name for node in tree.body
                    if isinstance(node, ast.ClassDef)
                    and any(
                        (isinstance(b, ast.Name) and b.id == "BaseModel")
                        or (isinstance(b, ast.Attribute) and b.attr == "BaseModel")
                        for b in node.bases
                    )
                }
                if not models:
                    continue

                for node in ast.walk(tree):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                        continue
                    if node.func.attr not in ("publish", "request") or not node.args:
                        continue
                    event_node = node.args[0]
                    if not (isinstance(event_node, ast.Constant) and isinstance(event_node.value, str)):
                        continue
                    event = event_node.value
                    if len(node.args) < 2:
                        continue
                    payload_node = node.args[1]

                    model_name = None
                    if (isinstance(payload_node, ast.Call)
                            and isinstance(payload_node.func, ast.Attribute)
                            and payload_node.func.attr == "model_dump"):
                        val = payload_node.func.value
                        if isinstance(val, ast.Call) and isinstance(val.func, ast.Name) and val.func.id in models:
                            model_name = val.func.id

                    if model_name:
                        entries.append({
                            "event": event,
                            "model": model_name,
                            "domain": domain,
                            "file": filename,
                        })

        return entries
