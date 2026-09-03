from microcoreos.base_plugin import BasePlugin


class MessageResendPlugin(BasePlugin):
    """
    Listens for 'message.resend' on the event_bus and forwards the message
    to the requested platform chat.

    Expected payload: {"platform": str, "channel_id": str, "channel_name": str, "message": str}
    """

    def __init__(self, event_bus, logger):
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("message.resend", self._on_resend)

    async def _on_resend(self, event):
        data = event.payload
        message = data.get("message", "")
        if not message:
            self.logger.warning(f"[MessageResend] Missing message: {data}")
            return
        await self.bus.publish("chat.message.send", {
            "platform": data.get("platform", "twitch"),
            "channel_id": data.get("channel_id") or data.get("channel"),
            "channel_name": data.get("channel_name") or data.get("channel"),
            "message": message,
        })
