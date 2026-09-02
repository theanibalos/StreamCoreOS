from core.base_plugin import BasePlugin


class YouTubeChatSendPlugin(BasePlugin):
    """Sends `chat.message.send` payloads whose platform is `youtube`."""

    def __init__(self, event_bus, youtube, logger):
        self.bus = event_bus
        self.youtube = youtube
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.message.send", self._send)

    async def _send(self, event):
        data = event.payload or {}
        if data.get("platform") != "youtube":
            return

        message = data.get("message") or ""
        live_chat_id = data.get("channel_id") or ""
        if not live_chat_id or not message:
            self.logger.warning(f"[YouTubeChatSend] Missing liveChatId/message: {data}")
            return

        try:
            await self.youtube.send_message(live_chat_id, message)
        except Exception as e:
            self.logger.error(f"[YouTubeChatSend] Failed sending to {live_chat_id}: {e}")
