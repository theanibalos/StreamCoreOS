import httpx
from core.base_plugin import BasePlugin

OLLAMA_URL = "http://192.168.1.137:11434/api/generate"
OLLAMA_MODEL = "lfm2:latest"

# El tema prohibido. CSS/HTML/JS en general están permitidos,
# solo se penaliza cuando el contexto es específicamente Tailwind.
BANNED_TOPICS = ["Tailwind CSS"]

SYSTEM_PROMPT = (
    "Eres un moderador de chat de Twitch. El único tema prohibido es Tailwind CSS. "
    "Responde SIEMPRE con un JSON válido con esta estructura exacta: "
    '{\"flagged\": true, \"reason\": \"motivo en español, máximo 8 palabras\"} '
    "si hay infracción, o "
    '{\"flagged\": false} '
    "si no la hay. Nada más fuera del JSON. "
    "\n"
    "Marca flagged=true si el mensaje hace referencia a Tailwind CSS, incluyendo: "
    "errores ortográficos (teilwind, taylwind, tailwnd), leet speak (t41lw1nd, t@ilw1nd), "
    "traducciones de tail+wind a cualquier idioma como palabra compuesta "
    "(español: colaviento, cola-viento, cola del viento; portugués: caldovento; etc.), "
    "la palabra en otros alfabetos o idiomas (japonés, griego, etc.), "
    "o la palabra codificada en hex, base64, ROT13 u otro sistema. "
    "\n"
    "Marca flagged=false si: hablan de CSS/HTML/JS en general, tailscale, fail2ban, "
    "Bootstrap, Bulma, animales, naturaleza, o cualquier cosa sin relación directa con Tailwind CSS. "
    "Ante la duda: flagged=false."
)


class AiModPlugin(BasePlugin):
    """
    Uses a local Ollama model to decide if a chat message mentions a banned topic.

    When flagged → sends a warning in chat. Does NOT ban or timeout.
    Moderators and the broadcaster are never checked.

    To change the banned topics, edit BANNED_TOPICS above.
    """

    def __init__(self, twitch, event_bus, logger):
        self.twitch = twitch
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.message.received", self._on_message)

    async def _on_message(self, msg: dict):
        if msg.get("is_broadcaster") or msg.get("is_mod"):
            return

        message = msg.get("message", "").strip()
        if not message or len(message) < 5:
            return

        display_name = msg.get("display_name", "")
        channel = msg.get("channel", "")

        try:
            detected_topic = await self._ask_ai(message)
        except Exception as e:
            self.logger.error(f"[AiMod] Ollama error: {e}")
            return

        if not detected_topic:
            return

        self.logger.warning(f"[AiMod] Flagged message from {display_name}: {message!r} (topic: {detected_topic})")
        await self.twitch.send_message(
            channel,
            f"@{display_name} ⚠️ Cuidado, puedes ser baneado por mencionar '{detected_topic}'.",
        )

    async def _ask_ai(self, message: str) -> str | None:
        """Returns the reason string if flagged, or None if clean."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(OLLAMA_URL, json={
                "model": OLLAMA_MODEL,
                "system": SYSTEM_PROMPT,
                "prompt": f"Mensaje: {message}",
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            })
            resp.raise_for_status()
            try:
                data = resp.json().get("response", "{}")
                self.logger.warning(data)
                if isinstance(data, str):
                    import json
                    data = json.loads(data)
                if not data.get("flagged", False):
                    return None
                return data.get("reason", "referencia a Tailwind CSS")
            except Exception:
                return None
