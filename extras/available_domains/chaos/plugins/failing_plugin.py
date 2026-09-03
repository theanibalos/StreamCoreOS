from microcoreos.base_plugin import BasePlugin

class FailingPlugin(BasePlugin):
    """
    A plugin that crashes when its endpoint is called, verifying 
    that the HTTP server and other plugins stay online.
    """
    def __init__(self, http, logger):
        self.http = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            path="/chaos/fail",
            method="GET",
            handler=self.handler,
            tags=["Chaos"]
        )

    async def handler(self, data, context=None):
        self.logger.warning("[FailingPlugin] Handling request... prepare for crash.")
        return await self.execute(data)

    async def execute(self, data: dict = None, context=None):
        # Intentional division by zero
        x = 1 / 0
        return {"result": x}
