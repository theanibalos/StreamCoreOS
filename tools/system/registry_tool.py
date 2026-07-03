from core.base_tool import BaseTool


class RegistryTool(BaseTool):
    """
    Proxy Tool that exposes the Core Registry and metrics to Plugins.
    Receives the registry and container references at registration time via Container,
    so they are available immediately in on_boot() — no timing dependency.
    """
    def __init__(self):
        self._core_registry = None
        self._container = None

    @property
    def name(self) -> str:
        return "registry"

    def _set_core_registry(self, registry):
        """Called by Container.register() before any plugin boots."""
        self._core_registry = registry

    def _set_container(self, container):
        """Called by Container.register() to enable metrics access."""
        self._container = container

    def setup(self):
        pass

    def get_interface_description(self) -> str:
        return """
        Systems Registry Tool (registry):
        - PURPOSE: Introspection and discovery of the system's architecture at runtime.
        - CAPABILITIES:
            - get_system_dump() -> dict: Full inventory of active Tools, Domains and Plugins.
                Returns:
                {
                  "tools": {
                    "<tool_name>": {"status": "OK"|"FAIL"|"DEAD", "message": str|None}
                  },
                  "plugins": {
                    "<PluginClassName>": {
                      "status": "BOOTING"|"RUNNING"|"READY"|"DEAD",
                      "error": str|None,
                      "domain": str,
                      "class": str,
                      "dependencies": ["tool_name", ...]  # tools injected in __init__
                    }
                  },
                  "domains": { ... }
                }
                NOTE: status is updated REACTIVELY via ToolProxy (hybrid policy):
                ToolUnavailableError -> DEAD immediately; any other exception ->
                DEAD only after 5 consecutive failures (success resets the streak).
                A tool that silently stopped responding may still show "OK".
            - get_domain_metadata() -> dict: Detailed analysis of models and schemas.
            - get_metrics() -> list[dict]: Last 1000 tool call records.
                Each record: {tool, method, duration_ms, success, timestamp}.
                Use to build /system/metrics or feed into an observability sink.
            - add_metrics_sink(callback): Register a sink for real-time metric records.
                Signature: callback(record: dict).
                Called synchronously on every tool method call — keep it fast.
            - update_tool_status(name, status, message=None): Manually override a tool's health status.
                status: "OK" | "FAIL" | "DEAD".
                Intended for health-check plugins that verify tools proactively.
        """

    def get_system_dump(self) -> dict:
        if not self._core_registry:
            return {"tools": {}, "domains": {}, "plugins": {}}
        return self._core_registry.get_system_dump()

    def get_domain_metadata(self) -> dict:
        if not self._core_registry:
            return {}
        return self._core_registry.get_domain_metadata()

    def get_metrics(self) -> list:
        if not self._container:
            return []
        return self._container.get_metrics()

    def add_metrics_sink(self, callback):
        if not self._container:
            return
        self._container.add_metrics_sink(callback)

    def update_tool_status(self, name: str, status: str, message: str = None):
        if not self._core_registry:
            return
        self._core_registry.update_tool_status(name, status, message)
