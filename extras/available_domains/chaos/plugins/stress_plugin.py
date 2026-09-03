import time
import asyncio
from microcoreos.base_plugin import BasePlugin

class StressPlugin(BasePlugin):
    """
    Demonstrates how the hybrid architecture handles heavy tasks.
    """
    def __init__(self, http, logger):
        self.http = http
        self.logger = logger

    async def on_boot(self):
        # 1. Synchronous Blocking (will be offloaded to threadpool by http tool)
        self.http.add_endpoint(
            path="/stress/sync",
            method="GET",
            handler=self.sync_heavy_task,
            tags=["Stress"]
        )
        
        # 2. Asynchronous Non-Blocking
        self.http.add_endpoint(
            path="/stress/async",
            method="GET",
            handler=self.async_long_task,
            tags=["Stress"]
        )

    def sync_heavy_task(self, data, context):
        """A 'Legacy' or CPU-bound task using standard time.sleep"""
        self.logger.info("[Stress] Starting SYNC heavy task (5s)...")
        time.sleep(5) 
        return {"status": "completed", "mode": "sync-threadpool"}

    async def async_long_task(self, data, context):
        """A modern async task using asyncio.sleep"""
        self.logger.info("[Stress] Starting ASYNC long task (5s)...")
        await asyncio.sleep(5)
        return {"status": "completed", "mode": "async-event-loop"}
