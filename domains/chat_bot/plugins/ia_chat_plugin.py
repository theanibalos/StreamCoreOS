import asyncio
from core.base_plugin import BasePlugin

MAX_RESPONSE_CHARS = 450  # Twitch chat limit is 500


class IAChatPlugin(BasePlugin):
    """
    Responds to !ia <question> in Twitch chat using the configured AI tool.
    Silently skips if the AI tool is not configured.
    Per-user cooldown is read from the AI config (chat_cooldown_s).
    """

    def __init__(self, twitch, event_bus, state, ai, logger):
        self.twitch = twitch
        self.bus = event_bus
        self.state = state
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.command.received", self._handle)

    async def _handle(self, data: dict):
        command = data.get("command", "").lower()
        if command != "!ia":
            return

        if not self.ai.is_configured():
            return

        question = data.get("args", "").strip()
        if not question:
            await self.twitch.send_message(
                data["channel"],
                f"@{data['display_name']} Escribe tu pregunta después de !ia 😊",
            )
            return

        user_id = data.get("user_id", "")
        cooldown_key = f"ia_cooldown:{user_id}"
        if self.state.get(cooldown_key, namespace="ia_chat"):
            return

        cooldown_s = self.ai.get_chat_cooldown()
        self.state.set(cooldown_key, True, namespace="ia_chat")
        asyncio.get_event_loop().call_later(
            cooldown_s,
            lambda: self.state.delete(cooldown_key, namespace="ia_chat"),
        )

        await self.twitch.send_message(
            data["channel"],
            f"@{data['display_name']} Pensando... 🤔",
        )

        try:
            personality = self.ai.get_chat_personality()
            answer = await self.ai.complete(
                messages=[{"role": "user", "content": question}],
                system=personality["system_prompt"],
                max_tokens=personality["max_tokens"],
                temperature=personality["temperature"],
            )
            reply = f"@{data['display_name']} {answer}"
            if len(reply) > MAX_RESPONSE_CHARS:
                reply = reply[: MAX_RESPONSE_CHARS - 1] + "…"
            await self.twitch.send_message(data["channel"], reply)
        except Exception as e:
            self.logger.error(f"[IAChatPlugin] {e}")
            await self.twitch.send_message(
                data["channel"],
                f"@{data['display_name']} No pude obtener respuesta. Inténtalo más tarde.",
            )
