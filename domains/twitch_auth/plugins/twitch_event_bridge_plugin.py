from core.base_plugin import BasePlugin

class TwitchEventBridgePlugin(BasePlugin):
    """
    Subscribes to all incoming Twitch EventSub events and publishes
    them to the application-wide EventBus so that other plugins (like
    WebhookExecutorPlugin) can consume them.
    """
    def __init__(self, twitch, event_bus, logger):
        self.twitch = twitch
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.twitch.on_event("*", self._forward_to_bus)
        self.logger.info("[TwitchEventBridge] Event bridge initialized.")

    async def _forward_to_bus(self, event_data: dict):
        event_type = event_data.get("_event_type", "twitch.event")
        # Extract the metadata tag and publish clean data
        clean_data = {k: v for k, v in event_data.items() if k != "_event_type"}
        self.logger.info(f"[TwitchEventBridge] Forwarding {event_type} to EventBus")
        await self.bus.publish(event_type, clean_data)
