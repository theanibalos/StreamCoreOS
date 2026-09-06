import ast
import inspect
import os

# ── Renderers: produce text ─────────────────────────────────────────────────
# Plain functions, not methods: ContextTool holds no instance state, so these
# never needed `self` — they only ever called each other.

def _clean_res_info(res_info: str) -> str:
    """Strips standard envelope wrapper boilerplate (success: bool, Optional, error: Optional[str])
    to present a clean, ultra-compact response payload model."""
    if "data: " in res_info:
        data_part = res_info.split("data: ", 1)[1]
        if ", error: " in data_part:
            data_part = data_part.rsplit(", error: ", 1)[0]
        data_part = data_part.strip()
        if data_part.startswith("Optional[") and data_part.endswith("]"):
            data_part = data_part[9:-1].strip()
        return data_part
    return res_info


def _load_authoring_guide() -> str:
    """The plugin authoring guide (executor rules + one template per
    deliverable type) is maintained next to this tool and embedded
    verbatim, so the manifest stays the single self-sufficient artifact
    for writing a plugin or its tests."""
    path = os.path.join(os.path.dirname(__file__), "authoring_guide.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() + "\n"
    except Exception as e:
        print(f"[ContextTool] Error reading authoring guide: {e}")
        return ""


def _generate_plugin_quick_start() -> str:
    return """## ⚡ Operating Context
This file contains the technical signature of active tools and domains in the system.
For plugin development guides, critical rules, and syntax examples, see [AGENTS.md](AGENTS.md).

---

"""


def _describe_models(domain: str) -> list[str]:
    """
    The domain's entity models — its UBIQUITOUS LANGUAGE.

    Deliberately NOT the table: the model is a design decision the plan
    makes, and it is supposed to differ from storage. `password_hash` is a
    column and must never be a model field; `roles` is `text` on disk and
    `list[str]` in the domain. That difference is exactly what tells a
    feature author what the API speaks and what it must never expose.

    Read from domains/{domain}/models/*.py. Not derivable from anything —
    which is why it is hand-written and why a plan declares it.
    """
    models_dir = os.path.join("domains", domain, "models")
    if not os.path.isdir(models_dir):
        return []

    described = []
    for filename in sorted(f for f in os.listdir(models_dir)
                           if f.endswith(".py") and f != "__init__.py"):
        path = os.path.join(models_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except Exception as e:
            print(f"[ContextTool] Error parsing model {path}: {e}")
            continue

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            fields = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    try:
                        type_str = ast.unparse(item.annotation)
                    except Exception:
                        type_str = "any"
                    fields.append(f"{item.target.id}: {type_str}")
            if fields:
                described.append(
                    f"**Model `{node.name}`** (domain vocabulary): {', '.join(fields)}"
                )
    return described


def _describe_table(schema: dict, table: str) -> str:
    """Renders one table's real columns. Never guesses: if the migration
    declares a table the database does not have, that is reported as-is —
    it means the migration did not run."""
    info = schema.get(table)
    if info is None:
        return "⚠️ declared in a migration but ABSENT from the live database"

    rendered = []
    for col in info.get("columns", []):
        flags = []
        if col.get("primary_key"):
            flags.append("PK")
        elif not col.get("nullable", True):
            flags.append("NOT NULL")
        if col.get("default") is not None:
            flags.append(f"default {col['default']}")
        suffix = ", " + ", ".join(flags) if flags else ""
        rendered.append(f"{col['name']} ({col['type']}{suffix})")

    line = ", ".join(rendered)
    for cols in info.get("unique", []):
        line += f" — UNIQUE({', '.join(cols)})"
    for fk in info.get("foreign_keys", []):
        line += (f" — FK {fk['column']} → "
                 f"{fk['references_table']}.{fk['references_column']}")
    return line


IGNORED_TOOL_METHODS = {
    "setup",
    "name",
    "get_interface_description",
    "on_boot_complete",
    "on_instrument",
    "shutdown",
    "on_boot",
}


def _generate_tool_signatures(raw_tool) -> str:
    """Derives a canonical public signature block from the real tool instance.

    Excludes private methods ('_*') and lifecycle plumbing (setup, shutdown, etc.).
    Preserves parameter names, defaults, keyword-only markers, and return annotations.
    Handles opaque callables by reporting '<signature unavailable>'.
    """
    lines = []
    # Inspect routine members sorted by name for deterministic order
    for name, func in sorted(inspect.getmembers(raw_tool, predicate=inspect.isroutine), key=lambda x: x[0]):
        if name.startswith("_") or name in IGNORED_TOOL_METHODS:
            continue
        try:
            sig = inspect.signature(func, follow_wrapped=True)
            params = [p for p_name, p in sig.parameters.items() if p_name not in ("self", "cls")]
            clean_sig = sig.replace(parameters=params)
            is_async = (
                inspect.iscoroutinefunction(func)
                or inspect.iscoroutinefunction(getattr(func, "__func__", None))
            )
            prefix = "async def " if is_async else "def "
            lines.append(f"{prefix}{name}{clean_sig}")
        except (ValueError, TypeError):
            lines.append(f"def {name}(...) -> <signature unavailable>")

    return "\n".join(lines)

