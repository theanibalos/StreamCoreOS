from core.base_plugin import BasePlugin

_MAX_CHAT_LEN = 490  # Twitch limit is 500; leave room for prefix


class CommandsListPlugin(BasePlugin):
    """
    Handles the !commands default command.

    Responds in chat with the names of all enabled commands.
    Splits into multiple messages if the list exceeds Twitch's 500-char limit.

    Userlevel: everyone.
    """

    def __init__(self, event_bus, db, twitch, logger):
        self.bus = event_bus
        self.db = db
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.command.received", self._on_command)

    async def _on_command(self, event):
        data = event.payload
        if data.get("command", "").lower() != "!commands":
            return

        channel = data.get("channel_name") or data.get("channel_id", "")

        try:
            rows = await self.db.query(
                "SELECT name FROM chat_commands WHERE enabled=1 ORDER BY name"
            )
            if not rows:
                await self._send(data, "No hay comandos activos.")
                return

            names = [r["name"] for r in rows]
            prefix = "Comandos: "
            messages = _build_messages(prefix, names)

            for msg in messages:
                await self._send(data, msg)
        except Exception as e:
            self.logger.error(f"[CommandsList] {e}")

    async def _send(self, data: dict, message: str):
        await self.bus.publish("chat.message.send", {
            "platform": data.get("platform", "twitch"),
            "channel_id": data.get("channel_id"),
            "channel_name": data.get("channel_name"),
            "message": message,
        })


def _build_messages(prefix: str, names: list[str]) -> list[str]:
    """Split command names into chat-sized messages."""
    messages = []
    current = prefix
    for name in names:
        segment = name + " "
        if len(current) + len(segment) > _MAX_CHAT_LEN:
            messages.append(current.rstrip())
            current = segment
        else:
            current += segment
    if current.strip():
        messages.append(current.rstrip())
    return messages
