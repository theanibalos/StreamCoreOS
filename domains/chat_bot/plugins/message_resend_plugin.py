from core.base_plugin import BasePlugin


class MessageResendPlugin(BasePlugin):
    """
    Listens for 'message.resend' on the event_bus and forwards the message
    to Twitch chat.

    Expected payload: {"channel": str, "message": str}
    """

    def __init__(self, twitch, event_bus, logger):
        self.twitch = twitch
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("message.resend", self._on_resend)

    async def _on_resend(self, event):
        data = event.payload
        channel = data.get("channel", "")
        message = data.get("message", "")

        if not channel or not message:
            self.logger.warning(f"[MessageResend] Missing channel or message: {data}")
            return

        try:
            await self.twitch.send_message(channel, message)
        except Exception as e:
            self.logger.error(f"[MessageResend] Failed to resend to '{channel}': {e}")
