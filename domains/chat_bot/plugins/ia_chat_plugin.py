import time
from core.base_plugin import BasePlugin

MAX_RESPONSE_CHARS = 450  # Twitch chat hard limit is 500


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

    async def _handle(self, event):
        data = event.payload
        if data.get("command", "").lower() != "!ia":
            return

        if not self.ai.is_configured():
            return

        if not await self.state.get("chat_ia_enabled", default=True, namespace="ia_config"):
            return

        question = data.get("args", "").strip()
        channel  = data["channel"]
        name     = data["display_name"]

        if not question:
            await self.twitch.send_message(
                channel,
                f"@{name} Escribe tu pregunta después de !ia.",
            )
            return

        # Per-user cooldown
        user_id     = data.get("user_id", "")
        cooldown_key = f"ia_cooldown:{user_id}"
        expires_at  = await self.state.get(cooldown_key, namespace="ia_chat")
        if expires_at:
            remaining = int(expires_at - time.time())
            if remaining > 0:
                await self.twitch.send_message(
                    channel,
                    f"@{name} Espera {remaining}s antes de volver a usar !ia.",
                )
                return

        cooldown_s = self.ai.get_chat_cooldown()
        # TTL-based expiry (state tool now supports it natively) instead of a
        # call_later + lambda — the lambda would return an un-awaited
        # coroutine now that state.set/delete are async.
        await self.state.set(cooldown_key, time.time() + cooldown_s, namespace="ia_chat", ttl=cooldown_s)

        await self.twitch.send_message(channel, f"@{name} Pensando...")

        try:
            personality = self.ai.get_chat_personality()
            answer = await self.ai.complete(
                messages=[{"role": "user", "content": question}],
                system=personality["system_prompt"],
                max_tokens=personality["max_tokens"],
                temperature=personality["temperature"],
            )
            reply = f"@{name} {answer}"
            if len(reply) > MAX_RESPONSE_CHARS:
                reply = reply[: MAX_RESPONSE_CHARS - 1] + "…"
            await self.twitch.send_message(channel, reply)

        except Exception as e:
            code = getattr(e, "code", "unknown")
            self.logger.error(f"[IAChatPlugin] [{code}] {e}")
            msg = _user_message_for_error(code, name)
            await self.twitch.send_message(channel, msg)


def _user_message_for_error(code: str, name: str) -> str:
    messages = {
        "rate_limited":        f"@{name} La IA está ocupada en este momento, intenta en un rato.",
        "timeout":             f"@{name} La IA tardó demasiado en responder. Inténtalo de nuevo.",
        "connection_error":    f"@{name} No pude conectar con la IA. Inténtalo más tarde.",
        "provider_unavailable":f"@{name} El servicio de IA no está disponible ahora.",
        "context_too_long":    f"@{name} Tu pregunta es demasiado larga, acórtala.",
        "auth_failed":         f"@{name} Hay un problema de configuración con la IA.",
        "model_not_found":     f"@{name} Hay un problema de configuración con la IA.",
        "empty_response":      f"@{name} El modelo no generó una respuesta. Inténtalo de nuevo.",
    }
    return messages.get(code, f"@{name} No pude obtener respuesta. Inténtalo más tarde.")
