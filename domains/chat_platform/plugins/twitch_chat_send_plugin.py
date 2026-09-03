from microcoreos.base_plugin import BasePlugin


class TwitchChatSendPlugin(BasePlugin):
    """Sends `chat.message.send` payloads whose platform is `twitch`."""

    def __init__(self, event_bus, twitch, logger):
        self.bus = event_bus
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.message.send", self._send)

    async def _send(self, event):
        data = event.payload or {}
        if data.get("platform") != "twitch":
            return

        message = data.get("message") or ""
        channel = data.get("channel_name") or data.get("channel_id") or ""
        if not channel or not message:
            self.logger.warning(f"[TwitchChatSend] Missing channel/message: {data}")
            return

        try:
            await self.twitch.send_message(channel, message)
        except Exception as e:
            self.logger.error(f"[TwitchChatSend] Failed sending to {channel}: {e}")
