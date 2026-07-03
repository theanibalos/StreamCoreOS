import asyncio
from core.base_plugin import BasePlugin


class ToolHealthPlugin(BasePlugin):
    """
    Proactively checks the health of tools that expose a health_check() method.
    Runs in the background and updates the registry so that silently-failing tools
    are detected before a real request hits them.

    Interval is controlled by the HEALTH_CHECK_INTERVAL env var (default: 30s).
    """

    def __init__(self, container, registry, logger, config):
        self.container = container
        self.registry = registry
        self.logger = logger
        self.config = config
        self._task = None

    async def on_boot(self):
        interval = int(self.config.get("HEALTH_CHECK_INTERVAL", default="30"))
        self._task = asyncio.create_task(self._run(interval))
        self.logger.info(f"[ToolHealth] Started — checking every {interval}s.")

    async def shutdown(self):
        if self._task:
            self._task.cancel()

    async def _run(self, interval: int):
        # Initial sleep so the first check happens after the system is fully booted.
        await asyncio.sleep(interval)
        while True:
            await self._check_all()
            await asyncio.sleep(interval)

    async def _check_all(self):
        for tool_name in self.container.list_tools():
            tool = self.container.get(tool_name)
            if hasattr(tool, "health_check") and callable(tool.health_check):
                await self._check(tool_name, tool.health_check)

    async def _check(self, tool_name: str, health_fn):
        try:
            ok = await health_fn()
            if ok:
                self.registry.update_tool_status(tool_name, "OK")
            else:
                self.registry.update_tool_status(tool_name, "FAIL", "health_check() returned False")
                self.logger.warning(f"[ToolHealth] '{tool_name}' health check failed.")
        except Exception as e:
            self.registry.update_tool_status(tool_name, "DEAD", str(e))
            self.logger.error(f"[ToolHealth] '{tool_name}' is unreachable: {e}")
