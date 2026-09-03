import time
from microcoreos.base_plugin import BasePlugin

class BlockingBootPlugin(BasePlugin):
    """
    This plugin simulates a heavy synchronous task (like video processing or 
    heavy DB migrations) during the on_boot phase.
    
    Thanks to the new Kernel Guard (asyncio.to_thread), this SHOULD NOT
    freeze the main system while it runs.
    """
    def __init__(self, logger):
        self.logger = logger

    def on_boot(self):
        self.logger.warning("[BlockingPlugin] I'm starting a HUGE 5-second SYNC task...")
        # This would normally freeze EVERYTHING in a pure-async system.
        time.sleep(5) 
        self.logger.info("[BlockingPlugin] Heavy task finished!")
