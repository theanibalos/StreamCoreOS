from microcoreos import BaseTool
from tools.context import renderers, scanners


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
            - Embeds the plugin authoring guide (tools/context/authoring_guide.md):
              executor rules plus one complete template per deliverable type, so the
              manifest alone is enough to write a plugin or its tests.
            - Regenerates AI_CONTEXT.md on every boot — always up to date with the live system.
        """

    async def on_boot_complete(self, container):
        registry = container.registry
        scanners._scan_domain_models(registry)
        self._generate_global_manifest(container, await self._fetch_live_schema(container))

    async def _fetch_live_schema(self, container) -> dict:
        """
        The real schema, read from the database itself.

        The manifest describes TABLES from here and never from the entity models:
        a model is a hand-written mirror and can drift (it did — the manifest used
        to name the table after the model FILE, so `scheduler_one_shots` was
        published as `scheduler_one_shot`). Introspection cannot drift: it reports
        what exists.

        Safe by the time this runs: migrations are applied in the db tool's
        setup(), and the Kernel awaits every setup() together before any
        on_boot_complete. A system with no db tool still gets its manifest.
        """
        try:
            return await container.get("db").describe_schema()
        except Exception as e:
            print(f"[ContextTool] Live schema unavailable, tables omitted from manifest: {e}")
            return {}

    # ── Global manifest ───────────────────────────────────────────────────────

    def _generate_global_manifest(self, container, schema: dict):
        manifest = "# 📜 SYSTEM MANIFEST\n\n"
        manifest += "> This file is ALL you need to build a plugin. For advanced topics (testing, observability, creating tools), see [INSTRUCTIONS_FOR_AI.md](INSTRUCTIONS_FOR_AI.md).\n\n"

        manifest += renderers._generate_plugin_quick_start()

        manifest += "## 🛠️ Quick Architecture Ref\n"
        manifest += "- **Pattern**: `__init__` (DI) -> `on_boot` (Register) -> handler methods (Action).\n"
        manifest += "- **Injection**: Tools are injected by name in the constructor.\n\n"

        manifest += "## 🛠️ Available Tools\n"
        manifest += "Check method signatures before implementation.\n\n"

        for name in container.list_tools():
            try:
                tool_proxy = container.get(name)
                raw_tool = (
                    tool_proxy._tool
                    if isinstance(getattr(tool_proxy, "_tool", None), BaseTool)
                    else tool_proxy
                )
                description = str(raw_tool.get_interface_description()).strip()
                if not description:
                    print(f"[ContextTool] WARNING: Tool '{name}' has no interface description. "
                          f"Update get_interface_description() in its class.")
                status_emoji = "✅" if tool_proxy else "❌"
                manifest += f"### 🔧 Tool: `{name}` (Status: {status_emoji})\n"

                signatures = renderers._generate_tool_signatures(raw_tool)
                if signatures:
                    manifest += "\n**Public Signatures:**\n```python\n"
                    manifest += signatures + "\n"
                    manifest += "```\n\n"

                manifest += "```text\n"
                manifest += description
                manifest += "\n```\n\n"
            except Exception as e:
                manifest += f"### 🔧 Tool: `{name}` (Status: ❌)\n"
                manifest += f"Error extracting info: {e}\n\n"

        manifest += "## 📦 Domains\n\n"

        # Two sources, each asked only what it alone can know: the migration path
        # says WHICH DOMAIN owns a table (the database has no notion of domains),
        # the live schema says WHAT THE TABLE IS.
        owned_tables = scanners._scan_migration_tables()

        dump = container.registry.get_system_dump()
        plugins_by_domain: dict[str, list[tuple[str, dict]]] = {}
        for plugin_name, info in dump.get("plugins", {}).items():
            domain = info.get("domain")
            if domain:
                plugins_by_domain.setdefault(domain, []).append((plugin_name, info))

        # The union, not just the plugin list. A domain that owns a table but
        # has no plugin yet is exactly what phase 0 produces, and listing only
        # registered plugins made that domain invisible in the one document
        # phase 0 is verified against: the migration applied, the manifest
        # regenerated, and the new table appeared nowhere. The table is the
        # deliverable — it belongs here the moment it exists.
        for domain in sorted(set(plugins_by_domain) | set(owned_tables)):
            plugins = plugins_by_domain.get(domain, [])
            plugin_names = [p[0] for p in plugins]

            all_deps: set[str] = set()
            for _, info in plugins:
                all_deps.update(info.get("dependencies", []))

            endpoints = scanners._get_domain_endpoints(domain)
            emitted_map = scanners._scan_published_events(domain)
            consumed = scanners._get_consumed_events(plugin_names, container)
            tables = owned_tables.get(domain, [])

            manifest += f"### `{domain}`\n"
            # Two lines, two questions. Table = storage, for writing SQL.
            # Model = the domain's vocabulary, for naming and shaping what the
            # API speaks. They differ on purpose (see renderers._describe_models).
            if tables:
                for table in tables:
                    manifest += f"- **Table `{table}`** (storage): {renderers._describe_table(schema, table)}\n"
            else:
                manifest += "- **Tables**: none\n"

            for model in renderers._describe_models(domain):
                manifest += f"- {model}\n"

            if endpoints:
                manifest += "- **Endpoints**:\n"
                for ep in endpoints:
                    if " (" in ep:
                        path_part, schema_part = ep.split(" (", 1)
                        manifest += f"  - `{path_part}`\n"
                        schema_part = schema_part.rstrip(")")
                        if "; res: " in schema_part:
                            req_info, res_info = schema_part.split("; res: ", 1)
                            req_info = req_info.replace("req: ", "", 1)
                            manifest += f"    - **req**: {req_info}\n"
                            manifest += f"    - **res**: {renderers._clean_res_info(res_info)}\n"
                        elif schema_part.startswith("req: "):
                            req_info = schema_part.replace("req: ", "", 1)
                            manifest += f"    - **req**: {req_info}\n"
                        elif schema_part.startswith("res: "):
                            res_info = schema_part.replace("res: ", "", 1)
                            manifest += f"    - **res**: {renderers._clean_res_info(res_info)}\n"
                    else:
                        manifest += f"  - `{ep}`\n"
            else:
                manifest += "- **Endpoints**: none\n"

            if emitted_map:
                emitted_strs = [f"`{name}` ({', '.join(sorted(keys))})" for name, keys in sorted(emitted_map.items())]
                manifest += f"- **Events emitted**: {', '.join(emitted_strs)}\n"
            else:
                manifest += "- **Events emitted**: none\n"

            manifest += f"- **Events consumed**: {', '.join(sorted(consumed)) if consumed else 'none'}\n"
            manifest += f"- **Dependencies**: {', '.join(sorted(all_deps)) if all_deps else 'none'}\n"
            if plugin_names:
                manifest += f"- **Plugins**: {', '.join(sorted(plugin_names))}\n\n"
            else:
                # Says which phase the domain is in, not merely that a list is
                # empty: this is the line a phase 0 author is looking for.
                manifest += ("- **Plugins**: none — phase 0 only (tables and "
                             "models exist, no feature implements them yet)\n\n")

        manifest += renderers._load_authoring_guide()

        try:
            with open("AI_CONTEXT.md", "w", encoding="utf-8") as f:
                f.write(manifest)
        except Exception as e:
            print(f"[ContextTool] Error writing AI_CONTEXT.md: {e}")
