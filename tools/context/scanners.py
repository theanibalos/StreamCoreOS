import ast
import os
import re

# Table ownership is read from the migration PATH, so only the NAME is parsed here.
# finditer (not search): one .sql file may declare several tables.
_CREATE_TABLE_RE = re.compile(
    r"""CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["'`\[]?([A-Za-z_][A-Za-z0-9_]*)""",
    re.IGNORECASE,
)


# ── Scanners: read the disk, return data ────────────────────────────────────
# Plain functions, not methods: ContextTool holds no instance state, so these
# never needed `self` — they only ever called each other.

def _scan_domain_models(registry):
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


def _extract_ast_models(tree: ast.AST) -> dict[str, str]:
    models: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            fields = {}
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    try:
                        type_str = ast.unparse(item.annotation)
                    except Exception:
                        type_str = "any"
                    fields[item.target.id] = type_str
            if fields:
                models[node.name] = fields

    formatted: dict[str, str] = {}
    sorted_model_names = sorted(models.keys(), key=len, reverse=True)
    for name, fields in models.items():
        field_strs = []
        for f_name, f_type in fields.items():
            for sub_name in sorted_model_names:
                if sub_name != name and re.search(r'\b' + re.escape(sub_name) + r'\b', f_type):
                    sub_f_str = ", ".join(f"{k}: {v}" for k, v in models[sub_name].items())
                    f_type = re.sub(r'\b' + re.escape(sub_name) + r'\b', f"{sub_name}({sub_f_str})", f_type)
            field_strs.append(f"{f_name}: {f_type}")
        formatted[name] = ", ".join(field_strs)
    return formatted


def _get_domain_endpoints(domain: str) -> list[str]:
    """
    AST analysis of plugin source files to extract endpoints and their request/response schemas.
    More robust than regex.
    """
    endpoints: set[str] = set()
    plugins_dir = os.path.join("domains", domain, "plugins")
    if not os.path.isdir(plugins_dir):
        return []

    for filename in os.listdir(plugins_dir):
        if not filename.endswith(".py"):
            continue
        filepath = os.path.join(plugins_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())

            ast_models = _extract_ast_models(tree)

            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    method_name = node.func.attr

                    # 1. add_endpoint
                    if method_name == "add_endpoint":
                        path, method = None, None
                        req_model_name, res_model_name = None, None

                        # Positional args
                        if len(node.args) >= 1 and isinstance(node.args[0], ast.Constant):
                            path = node.args[0].value
                        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                            method = node.args[1].value

                        # Keyword args
                        for kw in node.keywords:
                            if kw.arg == "path" and isinstance(kw.value, ast.Constant):
                                path = kw.value.value
                            if kw.arg == "method" and isinstance(kw.value, ast.Constant):
                                method = kw.value.value
                            if kw.arg == "request_model" and isinstance(kw.value, ast.Name):
                                req_model_name = kw.value.id
                            if kw.arg == "response_model" and isinstance(kw.value, ast.Name):
                                res_model_name = kw.value.id

                        if path and method:
                            schema_parts = []
                            if req_model_name and req_model_name in ast_models:
                                schema_parts.append(f"req: {ast_models[req_model_name]}")
                            if res_model_name and res_model_name in ast_models:
                                schema_parts.append(f"res: {ast_models[res_model_name]}")

                            if schema_parts:
                                endpoints.add(f"{method.upper()} {path} ({'; '.join(schema_parts)})")
                            else:
                                endpoints.add(f"{method.upper()} {path}")

                    # 2. SSE
                    elif method_name == "add_sse_endpoint":
                        path = None
                        if node.args and isinstance(node.args[0], ast.Constant): path = node.args[0].value
                        for kw in node.keywords:
                            if kw.arg == "path" and isinstance(kw.value, ast.Constant): path = kw.value.value
                        if path: endpoints.add(f"SSE {path}")

                    # 3. WS
                    elif method_name == "add_ws_endpoint":
                        path = None
                        if node.args and isinstance(node.args[0], ast.Constant): path = node.args[0].value
                        for kw in node.keywords:
                            if kw.arg == "path" and isinstance(kw.value, ast.Constant): path = kw.value.value
                        if path: endpoints.add(f"WS {path}")

        except Exception as e:
            print(f"[ContextTool] Error parsing AST for {filepath}: {e}")

    return sorted(endpoints)


def _get_consumed_events(plugin_names: list[str], container) -> set[str]:
    try:
        event_bus = container.get("event_bus")
        consumed = set()
        for event, subs in event_bus.get_subscribers().items():
            if event.startswith("_reply."):
                continue
            for sub in subs:
                # sub is "module.ClassName.method_name" (module-qualified
                # so derived consumer groups never collide across domains)
                parts = sub.split(".")
                if len(parts) < 3:
                    continue  # plain-function subscriber, not a plugin method
                sub_class = parts[-2]
                # plugin_names contains "domain.ClassName"
                if any(p.endswith(f".{sub_class}") or p == sub_class for p in plugin_names):
                    consumed.add(event)
                    break
        return consumed
    except Exception:
        return set()


def _scan_published_events(domain: str) -> dict[str, set[str]]:
    """
    AST analysis to find .publish() calls.
    Returns a dict: { "event.name": {"key1", "key2", ...} }
    """
    event_map: dict[str, set[str]] = {}
    plugins_dir = os.path.join("domains", domain, "plugins")
    if not os.path.isdir(plugins_dir):
        return event_map

    for filename in os.listdir(plugins_dir):
        if not filename.endswith(".py"):
            continue
        filepath = os.path.join(plugins_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())

            # Module-level class fields, so Payload(...).model_dump() publishes
            # resolve to the payload model's field names.
            class_fields: dict[str, set[str]] = {}
            for n in tree.body:
                if isinstance(n, ast.ClassDef):
                    fields = {
                        s.target.id for s in n.body
                        if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
                    }
                    if fields:
                        class_fields[n.name] = fields

            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "publish":
                        event_name, keys = None, set()

                        # First arg is event name
                        if node.args and isinstance(node.args[0], ast.Constant):
                            event_name = node.args[0].value

                        # Second arg is payload: dict literal or Payload(...).model_dump()
                        if len(node.args) >= 2:
                            payload = node.args[1]
                            if isinstance(payload, ast.Dict):
                                for k in payload.keys:
                                    if isinstance(k, ast.Constant):
                                        keys.add(str(k.value))
                            elif (isinstance(payload, ast.Call)
                                  and isinstance(payload.func, ast.Attribute)
                                  and payload.func.attr == "model_dump"
                                  and isinstance(payload.func.value, ast.Call)
                                  and isinstance(payload.func.value.func, ast.Name)):
                                keys.update(class_fields.get(payload.func.value.func.id, set()))

                        if event_name:
                            if event_name not in event_map:
                                event_map[event_name] = keys
                            else:
                                event_map[event_name].update(keys)
        except Exception:
            pass
    return event_map


def _scan_migration_tables() -> dict[str, list[str]]:
    """
    domain -> tables it declares, read from domains/{domain}/migrations/*.sql.

    Ownership is an architectural decision, so its source is the file PATH —
    the only place that records it. Only table NAMES are parsed, never
    columns: a later `ALTER TABLE ADD COLUMN` would make a parsed structure
    lie, while a name never moves. Structure comes from the live schema.
    """
    owned: dict[str, list[str]] = {}
    domains_dir = "domains"
    if not os.path.isdir(domains_dir):
        return owned

    for domain in sorted(os.listdir(domains_dir)):
        migrations_dir = os.path.join(domains_dir, domain, "migrations")
        if not os.path.isdir(migrations_dir):
            continue
        tables: list[str] = []
        for filename in sorted(f for f in os.listdir(migrations_dir) if f.endswith(".sql")):
            try:
                with open(os.path.join(migrations_dir, filename), "r", encoding="utf-8") as f:
                    sql = f.read()
            except Exception as e:
                print(f"[ContextTool] Error reading migration {filename}: {e}")
                continue
            for match in _CREATE_TABLE_RE.finditer(sql):
                name = match.group(1)
                if name not in tables:
                    tables.append(name)
        if tables:
            owned[domain] = sorted(tables)
    return owned
